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
from check_sqlite_payload import installed_packages


GOSU = "usr/local/bin/gosu"
TEST_CERTIFICATE = {
    "etc/ssl/private/ssl-cert-snakeoil.key", "etc/ssl/certs/ssl-cert-snakeoil.pem",
}
REMOVED_PACKAGES = {
    "libxml2", "libxslt1.1", "libllvm19", "libz3-4", "dirmngr", "gnupg-l10n",
    "gnupg-utils", "gpg-agent", "gpgconf", "gpgsm", "pinentry-curses",
    "libksba8", "libnpth0", "libassuan0", "libncursesw6",
    "libldap-2.5-0", "libsasl2-2", "libsasl2-modules-db",
}


def optional_feature_path(name):
    basename = PurePosixPath(name).name.removeprefix(".wh.")
    return (basename.startswith(("libxml2.so", "libxslt.so", "libexslt.so", "libLLVM", "libz3.so",
                                 "libldap", "liblber", "libsasl2.so"))
            or basename in {"pgxml.so", "llvmjit.so", "llvmjit_types.bc"}
            or "/postgresql/17/lib/bitcode/" in "/" + name
            or basename in {"dirmngr", "gpg-agent", "gpgconf", "gpgsm", "pinentry-curses"})


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
                        require(not optional_feature_path(name),
                                "Removed PostgreSQL feature retained in layers: " + name)
                        if name == "var/lib/dpkg/status":
                            require(item.isfile() and item.size < 16 * 1024 * 1024,
                                    "Invalid PostgreSQL package inventory")
                            with layer.extractfile(item) as content:
                                require(not REMOVED_PACKAGES & installed_packages(content.read()),
                                        "Removed PostgreSQL package retained in layers")
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
    expected_env = environments[0].copy()
    if "PG_VERSION" in expected_env:
        require(expected_env["PG_VERSION"] == "17.11-1.pgdg12+2", "Unexpected upstream PostgreSQL")
        expected_env["PG_VERSION"] += "+scoreit2"
    require(expected_env == environments[1], "PostgreSQL image environment changed")
    require(environments[1].get("PG_MAJOR") == "17", "Expected PostgreSQL 17")
    require(not {"GODEBUG", "OPENSSL_CONF"}.intersection(environments[1]),
            "Build-only TLS settings leaked into runtime")


def check_build_info(output):
    require(re.search(r"^\s*/probe/gosu:\s+go1[.]26[.]10$", output, re.M),
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

    def sql(container, statement, dbname=database):
        return docker("exec", "--env", "PGPASSWORD=" + password, "--user", "postgres",
                      container, "psql", "-X", "-A", "-t", "--host", "127.0.0.1",
                      "--set", "ON_ERROR_STOP=1", "--username", "postgres",
                      "--dbname", dbname, "--command", statement)

    def preflight(container, blocked=False):
        script = (Path(__file__).resolve().parents[1] /
                  'deploy/postgres_feature_preflight.sql').read_text()
        result = subprocess.run(
            ['docker', 'exec', '-i', '--env', 'PGPASSWORD=' + password,
             '--user', 'postgres', container, 'psql', '-X', '-h', '127.0.0.1',
             '-U', 'postgres', '-d', database],
            input=script, capture_output=True, text=True, timeout=15)
        if blocked:
            require(result.returncode != 0 and 'POSTGRES_PREFLIGHT_BLOCKED' in result.stderr,
                    'Feature preflight accepted an incompatible feature')
        else:
            require(result.returncode == 0 and 'POSTGRES_FEATURE_PREFLIGHT_OK' in result.stdout,
                    'Feature preflight rejected a compatible database')

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
        preflight(previous)
        sql(previous, 'CREATE TABLE xml_feature_probe (payload xml)')
        preflight(previous, blocked=True)
        sql(previous, 'DROP TABLE xml_feature_probe')
        original_hba = sql(previous, "SELECT pg_read_file(current_setting('hba_file'))")

        def write_hba(text):
            result = subprocess.run(
                ['docker', 'exec', '-i', '--user', 'postgres', previous, 'sh', '-ec',
                 'cat > "$PGDATA/pg_hba.conf"'], input=text, text=True,
                capture_output=True, timeout=10)
            require(result.returncode == 0, 'Could not write synthetic HBA fixture')

        try:
            write_hba(original_hba + '\nhost all all 192.0.2.0/24 ldap ldapserver=ldap.invalid\n')
            preflight(previous, blocked=True)
            write_hba(original_hba + '\nhost all all 192.0.2.0/24 invalid_method\n')
            preflight(previous, blocked=True)
        finally:
            write_hba(original_hba + '\n')
        preflight(previous)
        print('POSTGRES_FEATURE_PREFLIGHT_REJECTIONS_OK', flush=True)
        sql(previous, "CREATE TABLE runtime_probe (id integer PRIMARY KEY, note text NOT NULL); "
                      "INSERT INTO runtime_probe VALUES (101, 'проверка данных');")
        sql(previous, "CREATE COLLATION runtime_icu (provider=icu, locale='und'); "
                      "CREATE TABLE sort_probe (id integer PRIMARY KEY, value text NOT NULL); "
                      "INSERT INTO sort_probe SELECT n, v FROM unnest(ARRAY["
                      "'а','я','ё','е','Ё','Е','А','z','Z','a','A','Ä','ä','é','e','10','2',"
                      "'проект','Проект','задача','Задача','І','Ї','Й','И','🙂','']) "
                      "WITH ORDINALITY AS t(v,n); "
                      "CREATE INDEX sort_probe_libc ON sort_probe (value); "
                      "CREATE INDEX sort_probe_icu ON sort_probe (value COLLATE runtime_icu); "
                      "CREATE EXTENSION amcheck;")
        sort_query = ("SELECT string_agg(id::text,',' ORDER BY value,id) FROM sort_probe; "
                      "SELECT string_agg(id::text,',' ORDER BY value COLLATE runtime_icu,id) FROM sort_probe; "
                      "SELECT datlocprovider,datcollate,datctype,datcollversion,"
                      "pg_database_collation_actual_version(oid) FROM pg_database "
                      "WHERE datname=current_database(); "
                      "SELECT collversion,pg_collation_actual_version(oid) FROM pg_collation "
                      "WHERE collname='runtime_icu';")
        sort_before = sql(previous, sort_query)
        library_args = ("dpkg-query", "-W", "-f=${Package}=${Version}\\n",
                        "libc6", "libc-l10n", "locales", "libicu72", "tzdata")
        libraries_before = docker("exec", previous, *library_args)
        expected = "101:проверка данных"
        query = "SELECT id::text || ':' || note FROM runtime_probe ORDER BY id"
        require(sql(previous, query) == expected, "Test data was not written")
        docker("stop", "--time", "20", previous)
        require(json.loads(docker("inspect", previous))[0]["State"]["ExitCode"] == 0,
                "Previous PostgreSQL did not stop cleanly")

        current = create(target, "-current")
        ready(current)
        require(docker("exec", current, *library_args) == libraries_before,
                "Runtime libc/ICU/locales/tzdata versions changed")
        require(sql(current, sort_query) == sort_before, "Existing collations or sort order changed")
        require(sql(current, "SET enable_seqscan=off; " + sort_query).removeprefix("SET\n") == sort_before,
                "Index scans changed the existing sort results")
        sql(current, "SELECT bt_index_parent_check(indexrelid,true) FROM pg_index "
                     "WHERE indrelid IN ('runtime_probe'::regclass,'sort_probe'::regclass)")
        print("POSTGRES_COLLATIONS_AND_INDEXES_OK", flush=True)
        require(sql(current, "SELECT pg_jit_available()") == "f", "LLVM/JIT is still enabled")
        result = subprocess.run(
            ["docker", "exec", "--env", "PGPASSWORD=" + password, "--user", "postgres", current,
             "psql", "-X", "-h", "127.0.0.1",
             "-U", "postgres", "-d", database, "--set", "ON_ERROR_STOP=1", "-c", "SELECT XMLPARSE(DOCUMENT '<probe/>')"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
        )
        require(result.returncode == 1 and "unsupported XML feature" in result.stderr,
                "SQL/XML has not been disabled")
        print("POSTGRES_OPTIONAL_FEATURES_DISABLED_OK", flush=True)
        require(sql(current, query) == expected, "Existing PostgreSQL data changed")
        sql(current, "INSERT INTO runtime_probe VALUES (202, 'запись после замены');")
        expected += "\n202:запись после замены"
        require(sql(current, query) == expected, "Replacement PostgreSQL cannot write")
        print("POSTGRES_EXISTING_VOLUME_OK", flush=True)
        # Exercise the rebuilt libpq over TLS/TCP, including certificate trust.
        # These keys exist only in the uniquely named synthetic test volume.
        docker('exec', '--user', 'postgres', current, 'sh', '-ec',
               'umask 077; openssl req -x509 -newkey rsa:2048 -nodes -days 1 '
               '-subj /CN=score-it-postgres-test -addext subjectAltName=IP:127.0.0.1 '
               '-keyout "$PGDATA/server.key" -out "$PGDATA/server.crt"')
        sql(current, "ALTER SYSTEM SET ssl='on'")
        docker('restart', '--time', '20', current)
        ready(current)
        tls_args = ['docker', 'exec', '--env', 'PGPASSWORD=' + password,
                    '--env', 'PGSSLMODE=verify-full', '--user', 'postgres', current,
                    'sh', '-ec', 'export PGSSLROOTCERT="$PGDATA/server.crt"; exec "$@"', 'tls-probe']
        result = subprocess.run(
            [*tls_args, 'psql', '-X', '-A', '-t', '-h', '127.0.0.1', '-U', 'postgres',
             '-d', database, '-c', 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        require(result.returncode == 0 and result.stdout.strip() == 't',
                'Rebuilt libpq could not verify the TLS server certificate')
        bad_trust = subprocess.run(
            ['docker', 'exec', '--env', 'PGPASSWORD=' + password, '--env', 'PGSSLMODE=verify-full',
             '--env', 'PGSSLROOTCERT=/etc/ssl/certs/ca-certificates.crt', '--user', 'postgres', current,
             'psql', '-X', '-h', '127.0.0.1', '-U', 'postgres', '-d', database, '-c', 'SELECT 1'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        require(bad_trust.returncode != 0 and 'certificate verify failed' in bad_trust.stderr.lower(),
                'Rebuilt libpq accepted an untrusted TLS server certificate')
        print('POSTGRES_TLS_CLIENT_TRUST_OK', flush=True)
        restored_database = database + '_restored'
        docker('exec', '--env', 'PGPASSWORD=' + password, '--env', 'PGSSLMODE=verify-full',
               '--user', 'postgres', current,
               'sh', '-ec',
               'export PGSSLROOTCERT="$PGDATA/server.crt"; '
               'pg_dump -h 127.0.0.1 -U postgres -Fc -d "$1" -f /tmp/score-it-probe.dump; '
               'createdb -h 127.0.0.1 -U postgres "$2"; '
               'pg_restore -h 127.0.0.1 -U postgres --exit-on-error -d "$2" /tmp/score-it-probe.dump; '
               'rm /tmp/score-it-probe.dump', 'probe', database, restored_database)
        require(sql(current, query, restored_database) == expected,
                'Client dump/restore changed the test data')
        require(sql(current, sort_query, restored_database) == sort_before,
                'Client dump/restore changed collations or indexes')
        print('POSTGRES_CLIENT_DUMP_RESTORE_OK', flush=True)
        docker("restart", "--time", "20", current)
        ready(current)
        require(sql(current, query) == expected, "PostgreSQL data did not persist after restart")
        print("POSTGRES_RESTART_OK", flush=True)
        docker("stop", "--time", "20", current)
        rollback = create(source, "-rollback")
        ready(rollback)
        require(sql(rollback, query) == expected and sql(rollback, sort_query) == sort_before,
                "Previous server cannot reopen candidate data")
        print("POSTGRES_PREVIOUS_SERVER_REOPEN_OK", flush=True)
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
    output = docker("run", "--rm", "--pull=never", "--network", "none", "--read-only",
                    "--entrypoint", "sh", target["Id"], "-ec",
                    "cat /usr/share/score-it/postgres-configure.txt /usr/share/score-it/libpq-configure.txt; "
                    "for config in /usr/share/score-it/postgres-configure.txt /usr/share/score-it/libpq-configure.txt; do "
                    "grep -F -- --without-ldap \"$config\" >/dev/null; "
                    "grep -F -- --with-openssl \"$config\" >/dev/null; "
                    "grep -F -- --with-gssapi \"$config\" >/dev/null; done; "
                    "test \"$(dpkg-query -W -f='${Version}' libpq5)\" = 18.6-1.pgdg12+2+scoreit1; "
                    "test -z \"$(dpkg --audit)\"; "
                    "ldd /usr/lib/x86_64-linux-gnu/libpq.so.5; "
                    "find /usr/lib/postgresql/17 -type f \\( -name '*.so' -o -name postgres \\) "
                    "-exec ldd '{}' ';' 2>&1")
    for flag in ("--without-libxml", "--without-libxslt", "--without-llvm", "--without-ldap"):
        require(flag in output, "PostgreSQL configure flag is missing: " + flag)
    require("not found" not in output, "A PostgreSQL shared library cannot be loaded")
    require(not re.search(r'lib(?:ldap|lber|sasl2)', output), 'LDAP is still linked by PostgreSQL/libpq')
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
        print("POSTGRES_LDAP_DEPENDENCIES_ABSENT_OK", flush=True)
    check_existing_volume(source["Id"], target["Id"])
    print("POSTGRES_RUNTIME_OK", flush=True)


if __name__ == "__main__":
    main()
