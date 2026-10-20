#!/usr/bin/env python3
"""Check saved PostgreSQL layers and reuse a disposable PostgreSQL 17 volume.

All images must already be loaded. Only uniquely named temporary containers and
a new volume are used, with no network or host ports. No production data is read.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import struct
import subprocess
import tarfile
import tempfile
import time
import uuid

# Reuse the tested Docker-save/OCI archive traversal and Docker command wrapper.
from check_caddy_runtime import Archive, docker, layer_names, require


GOSU = "usr/local/bin/gosu"
TEST_CERTIFICATE = {
    "etc/ssl/private/ssl-cert-snakeoil.key", "etc/ssl/certs/ssl-cert-snakeoil.pem",
}


def check_archive(path, image):
    binary = None
    with Archive(path) as source:
        _, layers = layer_names(source, image, "linux/amd64")
        for name in layers:
            member = source.members.get(str(PurePosixPath(name)))
            require(member is not None and member.isfile(), "Missing image layer")
            with source.tar.extractfile(member) as stream:
                with tarfile.open(fileobj=stream, mode="r|*") as layer:
                    for item in layer:
                        name = str(PurePosixPath(item.name))
                        require(not PurePosixPath(name).is_absolute()
                                and ".." not in PurePosixPath(name).parts,
                                "Unsafe layer path")
                        require(name not in TEST_CERTIFICATE,
                                "Image-wide test certificate/key retained in layers")
                        require(name != "usr/local/bin/.wh.gosu", "Deleted gosu retained in layers")
                        if name != GOSU:
                            continue
                        require(item.isfile() and item.mode & 0o111, "gosu must be executable")
                        require(binary is None, "Overwritten gosu retained in layers")
                        with layer.extractfile(item) as content:
                            binary = content.read()
    require(binary is not None, "gosu is missing")
    require(len(binary) >= 64 and binary[:6] == b"\x7fELF\x02\x01",
            "Expected a little-endian ELF64 gosu")
    offset = struct.unpack_from("<Q", binary, 32)[0]
    size, count = struct.unpack_from("<HH", binary, 54)
    require(size >= 4 and offset + size * count <= len(binary), "Invalid ELF program headers")
    require(all(struct.unpack_from("<I", binary, offset + i * size)[0] != 3
                for i in range(count)), "gosu requires a dynamic loader")
    return binary


def check_metadata(source, target):
    defaults = {
        "Entrypoint": [], "Cmd": [], "User": "", "WorkingDir": "/",
        "StopSignal": "", "Volumes": {}, "ExposedPorts": {},
        "Healthcheck": {}, "Shell": [], "OnBuild": [],
    }
    for key, default in defaults.items():
        require((source.get(key) or default) == (target.get(key) or default),
                "PostgreSQL image configuration changed: " + key)
    environments = [dict(value.split("=", 1) for value in config.get("Env", []))
                    for config in (source, target)]
    require(environments[0] == environments[1], "PostgreSQL image environment changed")
    require(environments[1].get("PG_MAJOR") == "17", "Expected PostgreSQL 17")
    require(not {"GODEBUG", "OPENSSL_CONF"}.intersection(environments[1]),
            "Build-only TLS settings leaked into runtime")


def check_build_info(output):
    require(re.search(r"^\s*/probe/gosu:\s+go1[.]26[.]9$", output, re.M),
            "Unexpected gosu Go version")
    for module, version in (("github.com/moby/sys/user", "v0.4.1"),
                            ("golang.org/x/sys", "v0.48.0")):
        require(re.search(r"^\s*dep\s+" + re.escape(module) + r"\s+"
                          + re.escape(version) + r"\s", output, re.M),
                "Unexpected gosu module version: " + module)


def check_existing_volume(source, target):
    prefix = "score-it-postgres-runtime-" + uuid.uuid4().hex
    volume = prefix + "-data"
    containers = []
    volume_created = False
    database = "score_it_runtime_check"
    password = secrets.token_hex(24)

    def create(image, suffix):
        container = docker(
            "create", "--pull=never", "--network", "none", "--name", prefix + suffix,
            "--env", "POSTGRES_DB=" + database, "--env", "POSTGRES_PASSWORD=" + password,
            "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data", image,
        )
        containers.append(container)
        docker("start", container)
        return container

    def sql(container, statement):
        return docker("exec", "--env", "PGPASSWORD=" + password, "--user", "postgres",
                      container, "psql", "-X", "-A", "-t", "--host", "127.0.0.1",
                      "--set", "ON_ERROR_STOP=1", "--username", "postgres",
                      "--dbname", database, "--command", statement)

    def ready(container):
        deadline = time.monotonic() + 45
        while True:
            result = subprocess.run(
                # The entrypoint's temporary init server has no TCP listener.
                # Requiring TCP avoids mistaking it for the final server.
                ["docker", "exec", "--env", "PGPASSWORD=" + password,
                 "--user", "postgres", container, "psql", "-X", "-A", "-t", "--host", "127.0.0.1",
                 "--username", "postgres", "--dbname", database, "--command", "SELECT 1"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                break
            require(time.monotonic() < deadline, "Temporary PostgreSQL did not become ready")
            time.sleep(0.2)

    try:
        docker("volume", "create", volume)
        volume_created = True
        previous = create(source, "-previous")
        ready(previous)
        sql(previous, "CREATE TABLE runtime_probe (id integer PRIMARY KEY, note text NOT NULL); "
                      "INSERT INTO runtime_probe VALUES (101, 'проверка данных');")
        expected = "101:проверка данных"
        query = "SELECT id::text || ':' || note FROM runtime_probe ORDER BY id"
        require(sql(previous, query) == expected, "Test data was not written")
        docker("stop", "--time", "20", previous)
        require(json.loads(docker("inspect", previous))[0]["State"]["ExitCode"] == 0,
                "Previous PostgreSQL did not stop cleanly")

        current = create(target, "-current")
        ready(current)
        require(sql(current, query) == expected, "Existing PostgreSQL data changed")
        sql(current, "INSERT INTO runtime_probe VALUES (202, 'запись после замены');")
        expected += "\n202:запись после замены"
        require(sql(current, query) == expected, "Replacement PostgreSQL cannot write")
        print("POSTGRES_EXISTING_VOLUME_OK", flush=True)
        docker("restart", "--time", "20", current)
        ready(current)
        require(sql(current, query) == expected, "PostgreSQL data did not persist after restart")
        print("POSTGRES_RESTART_OK", flush=True)
    finally:
        for container in reversed(containers):
            subprocess.run(["docker", "rm", "-f", container], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=30)
        if volume_created:
            subprocess.run(["docker", "volume", "rm", volume], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--source-image", required=True, help="Pinned upstream PostgreSQL image")
    parser.add_argument("--go-image", required=True, help="Loaded Go image for build-info inspection")
    args = parser.parse_args()
    target = json.loads(docker("image", "inspect", args.image))[0]
    source = json.loads(docker("image", "inspect", args.source_image))[0]
    toolchain = json.loads(docker("image", "inspect", args.go_image))[0]
    check_metadata(source["Config"], target["Config"])
    print("POSTGRES_IMAGE_CONFIG_OK", flush=True)
    with tempfile.TemporaryDirectory(prefix="score-it-postgres-runtime-") as temp:
        directory = Path(temp)
        archive = directory / "postgres.tar"
        docker("image", "save", "--output", str(archive), args.image, timeout=120)
        binary = check_archive(archive, args.image)
        require(json.loads(docker("image", "inspect", args.image))[0]["Id"] == target["Id"],
                "PostgreSQL tag changed during export")
        binary_path = directory / "gosu"
        binary_path.write_bytes(binary)
        output = docker("run", "--rm", "--pull=never", "--network", "none", "--read-only",
                        "--mount", f"type=bind,source={binary_path},target=/probe/gosu,readonly",
                        "--entrypoint", "go", toolchain["Id"], "version", "-m", "/probe/gosu")
        check_build_info(output)
        runtime_hash = docker("run", "--rm", "--pull=never", "--network", "none",
                              "--entrypoint", "sha256sum", target["Id"], "/usr/local/bin/gosu")
        require(runtime_hash.split()[0] == hashlib.sha256(binary).hexdigest(),
                "Saved gosu differs from runtime gosu")
        print("POSTGRES_ARCHIVE_PAYLOAD_OK", flush=True)
    check_existing_volume(source["Id"], target["Id"])
    print("POSTGRES_RUNTIME_OK", flush=True)


if __name__ == "__main__":
    main()
