#!/usr/bin/env python3
"""Reject SQLite bytes in every saved layer of PostgreSQL-only runtime images."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

from check_caddy_runtime import Archive, docker, layer_names, require
from check_app_payload import check_app_archive
from check_runtime_tools import check_runtime_binary, check_runtime_member, check_runtime_packages

STATUS = "var/lib/dpkg/status"

def sqlite_path(name):
    basename = PurePosixPath(name).name.removeprefix(".wh.")
    return (
        (basename.startswith("libsqlite3") and (".so" in basename or basename.endswith((".a", ".la"))))
        or (basename.startswith("_sqlite3") and basename.endswith(".so"))
        or bool(re.search(r"/lib/python[^/]+/(?:site-packages/)?sqlite3(?:/|$)", "/" + name))
        or name in ("usr/bin/sqlite3", "usr/local/bin/sqlite3")
    )

def installed_packages(raw):
    packages = set()
    for block in raw.decode("utf-8").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(" "))
        if fields.get("Status") == "install ok installed":
            packages.add(fields.get("Package", ""))
    return packages

def check_archive(path, image, postgres=False, platform="linux/amd64"):
    statuses = []
    elf_count = 0
    with Archive(path) as source:
        _, layers = layer_names(source, image, platform)
        for layer_name in layers:
            member = source.members.get(str(PurePosixPath(layer_name)))
            require(member is not None and member.isfile(), "Missing runtime image layer")
            with source.tar.extractfile(member) as stream:
                with tarfile.open(fileobj=stream, mode="r|*") as layer:
                    for item in layer:
                        check_runtime_member(item)
                        name = str(PurePosixPath(item.name))
                        require(not PurePosixPath(name).is_absolute()
                                and ".." not in PurePosixPath(name).parts, "Unsafe runtime path")
                        require(not sqlite_path(name), "SQLite retained in image layers: " + name)
                        if postgres:
                            require(name not in ("usr/bin/gpg", "usr/bin/.wh.gpg"),
                                    "Unused gpg retained in PostgreSQL layers")
                        if name == STATUS:
                            require(item.isfile() and item.size < 16 * 1024 * 1024,
                                    "Invalid runtime package database")
                            with layer.extractfile(item) as content:
                                statuses.append(installed_packages(content.read()))
                        elif item.isfile():
                            with layer.extractfile(item) as content:
                                first = content.read(4)
                                if first == b'\x7fELF':
                                    require(item.size < 256 * 1024 * 1024, 'Oversized runtime ELF file')
                                    check_runtime_binary(first + content.read())
                                    elf_count += 1
    require(bool(statuses), "Runtime package inventory is missing")
    for packages in statuses:
        check_runtime_packages(packages)
        require("libsqlite3-0" not in packages, "SQLite package retained in image layers")
        if postgres:
            require(not {"gpg", "gnupg", "gpg-wks-client", "gpg-wks-server"} & packages,
                    "Unused gpg package retained in PostgreSQL layers")
    return elf_count


BASE_COMMANDS = r'''
set -eu
test "$(id -u)" = "$1"
probe_dir="$(mktemp -d)"
trap 'rm -rf "$probe_dir"' EXIT
printf 'source\n' >"$probe_dir/input"
cp "$probe_dir/input" "$probe_dir/copied"
mv "$probe_dir/copied" "$probe_dir/moved"
sed 's/source/checked/' "$probe_dir/moved" >"$probe_dir/result"
tar -C "$probe_dir" -cf "$probe_dir/roundtrip.tar" result
mkdir "$probe_dir/restored"
tar -C "$probe_dir/restored" -xf "$probe_dir/roundtrip.tar"
cmp "$probe_dir/result" "$probe_dir/restored/result"
test "$(cat "$probe_dir/restored/result")" = checked
printf 'RUNTIME_BASE_COMMANDS_OK uid=%s\n' "$(id -u)"
'''

from runtime_policy import APP_HEALTHCHECK, health_matches

def check_app_metadata(source, target):
    expected_env = dict(v.split("=", 1) for v in source.get("Env", []))
    expected_env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
                        SCORE_IT_REQUIRE_POSTGRESQL="1", DB_ENGINE="postgresql")
    actual_env = dict(v.split("=", 1) for v in target.get("Env", []))
    require(actual_env == expected_env, "Application base environment changed")
    for key, expected in {
        "User": "10001:10001", "WorkingDir": "/app",
        "Cmd": ["gunicorn", "--config", "deploy/gunicorn.conf.py", "config.wsgi:application"],
        "ExposedPorts": {"8000/tcp": {}},
    }.items():
        require(target.get(key) == expected, "Application runtime configuration changed: " + key)
    require(health_matches(target.get("Healthcheck"),APP_HEALTHCHECK), "Unexpected application healthcheck")
    for key in ("Entrypoint", "Volumes", "StopSignal", "OnBuild", "Shell"):
        require(not target.get(key), "Unexpected application runtime configuration: " + key)

APP_CHECK = r"""
import importlib.util, os, ssl
from pathlib import Path
from config.database import database_config
from django.core.exceptions import ImproperlyConfigured
assert os.geteuid() == 10001
assert os.environ['SCORE_IT_REQUIRE_POSTGRESQL'] == '1'
assert os.environ['DB_ENGINE'] == 'postgresql'
assert (Path('/app/data').stat().st_uid, Path('/app/data').stat().st_gid) == (10001, 10001)
assert all(importlib.util.find_spec(n) is None for n in ('sqlite3','_sqlite3','pysqlite3','pip','ensurepip'))
assert Path(ssl.get_default_verify_paths().cafile).is_file()
assert {p.name for p in Path('/app').iterdir()} == {
    'config', 'poker', 'static', 'staticfiles', 'templates', 'deploy', 'data', 'manage.py'}
assert not Path('/app/poker/tests').exists()
try:
    database_config({'SCORE_IT_REQUIRE_POSTGRESQL':'1','DB_ENGINE':'sqlite'}, Path('/tmp'))
except ImproperlyConfigured:
    pass
else:
    raise AssertionError('Production runtime accepted SQLite')
print('APP_POSTGRESQL_ONLY_OK')
"""

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-image", required=True)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--python-source-image", required=True)
    parser.add_argument("--platform", default="linux/amd64")
    args = parser.parse_args()
    app = json.loads(docker("image", "inspect", args.app_image))[0]
    source = json.loads(docker("image", "inspect", args.python_source_image))[0]
    check_app_metadata(source["Config"], app["Config"])
    print("APP_IMAGE_CONFIG_OK", flush=True)
    with tempfile.TemporaryDirectory(prefix="score-it-sqlite-payload-") as temp:
        for image, name, postgres in ((args.app_image, "app", False),
                                      (args.postgres_image, "postgres", True)):
            identity = json.loads(docker("image", "inspect", image))[0]["Id"]
            archive = Path(temp) / (name + ".tar")
            docker("image", "save", "--platform", args.platform, "--output", str(archive), image, timeout=120)
            elf_count = check_archive(archive, image, postgres=postgres, platform=args.platform)
            require(elf_count > 0, 'Runtime ELF dependency inventory is missing')
            if not postgres:
                check_app_archive(archive, image, args.platform)
                print('APP_ARCHIVE_PAYLOAD_OK', flush=True)
            require(json.loads(docker("image", "inspect", image))[0]["Id"] == identity,
                    "Runtime image tag changed during export")
            archive.unlink()
            print("SQLITE_ARCHIVE_PAYLOAD_OK=" + name, flush=True)
            print("RUNTIME_TOOLS_ARCHIVE_OK=" + name, flush=True)
            print("UNUSED_MOUNT_LIBRARIES_ARCHIVE_OK=" + name + " elf_files=" + str(elf_count), flush=True)
            uid = '999' if postgres else '10001'
            output = docker('run', '--rm', '--pull=never', '--network', 'none', '--read-only',
                            '--user', uid + ':' + uid, '--cap-drop', 'ALL',
                            '--security-opt', 'no-new-privileges:true', '--memory', '128m', '--pids-limit', '32',
                            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16m', '--entrypoint', 'sh',
                            identity, '-ec', BASE_COMMANDS, 'sh', uid)
            require(output == 'RUNTIME_BASE_COMMANDS_OK uid=' + uid, 'Base command runtime check differs')
            print(output + ' profile=' + name, flush=True)
    print(docker("run", "--rm", "--pull=never", "--network", "none", "--read-only",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                 "--entrypoint", "python", app["Id"], "-c", APP_CHECK), flush=True)

if __name__ == "__main__":
    main()

