"""Reject obsolete gosu, deleted test keys and incompatible PostgreSQL metadata."""

import copy
from pathlib import Path
import tempfile
import unittest

from check_postgres_runtime import GOSU, check_archive, check_build_info, check_metadata
from test_caddy_payload import binary, layer_bytes, saved_image


IMAGE = "score-it-postgres:payload-test"
BUILD_INFO = """/probe/gosu: go1.26.9
    path github.com/tianon/gosu
    dep github.com/moby/sys/user v0.4.1 h1:example
    dep golang.org/x/sys v0.48.0 h1:example
"""


class PostgresPayloadTests(unittest.TestCase):
    def verify(self, layers, oci=False):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "postgres.tar"
            saved_image(path, layers, oci=oci, image=IMAGE)
            return check_archive(path, IMAGE)

    def test_accepts_classic_and_oci_payloads(self):
        for oci in (False, True):
            with self.subTest(oci=oci):
                self.assertEqual(self.verify([layer_bytes({GOSU: binary()}, link=False)], oci),
                                 binary())

    def test_rejects_original_gosu_under_a_new_binary(self):
        for oci in (False, True):
            with self.subTest(oci=oci):
                with self.assertRaisesRegex(RuntimeError, "Overwritten gosu"):
                    self.verify([layer_bytes({GOSU: b"original gosu"}, link=False),
                                 layer_bytes({GOSU: binary()}, link=False)], oci)

    def test_rejects_image_wide_key_even_after_deletion(self):
        with self.assertRaisesRegex(RuntimeError, "certificate/key retained"):
            self.verify([
                layer_bytes({"etc/ssl/private/ssl-cert-snakeoil.key": b"test-only"}, link=False),
                layer_bytes({"etc/ssl/private/.wh.ssl-cert-snakeoil.key": b"", GOSU: binary()},
                            link=False),
            ])

    def test_rejects_deleted_gosu_and_missing_gosu(self):
        for files, error in (({"usr/local/bin/.wh.gosu": b""}, "Deleted gosu"),
                             ({"etc/os-release": b"debian"}, "gosu is missing")):
            with self.subTest(error=error):
                with self.assertRaisesRegex(RuntimeError, error):
                    self.verify([layer_bytes(files, link=False)])

    def test_rejects_dynamic_loader_dependency(self):
        with self.assertRaisesRegex(RuntimeError, "dynamic loader"):
            self.verify([layer_bytes({GOSU: binary(interpreter=True)}, link=False)])

    def test_rejects_optional_libraries_even_after_deletion(self):
        for name in ("usr/lib/x86_64-linux-gnu/libxml2.so.2.9.14",
                     "usr/lib/x86_64-linux-gnu/libLLVM-19.so.1",
                     "usr/lib/postgresql/17/lib/pgxml.so",
                     "usr/lib/postgresql/17/lib/llvmjit.so"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Removed PostgreSQL feature"):
                self.verify([layer_bytes({name: b"old"}, link=False),
                             layer_bytes({str(Path(name).parent / (".wh." + Path(name).name)): b"",
                                          GOSU: binary()}, link=False)])

    def test_rejects_removed_package_inventory(self):
        with self.assertRaisesRegex(RuntimeError, "Removed PostgreSQL package"):
            self.verify([layer_bytes({GOSU: binary(), "var/lib/dpkg/status":
                                     b"Package: libxml2\nStatus: install ok installed\n\n"}, link=False)])

    def test_rejects_obsolete_go_or_module_versions(self):
        check_build_info(BUILD_INFO)
        for before, after in (("go1.26.9", "go1.24.6"), ("v0.4.1", "v0.1.0"),
                              ("v0.48.0", "v0.1.0")):
            with self.subTest(before=before):
                with self.assertRaisesRegex(RuntimeError, "Unexpected gosu"):
                    check_build_info(BUILD_INFO.replace(before, after))

    def test_preserves_startup_storage_shutdown_and_environment(self):
        source = {
            "Entrypoint": ["docker-entrypoint.sh"], "Cmd": ["postgres"],
            "Env": ["PG_MAJOR=17", "PGDATA=/var/lib/postgresql/data", "LANG=en_US.utf8"],
            "StopSignal": "SIGINT", "Volumes": {"/var/lib/postgresql/data": {}},
            "ExposedPorts": {"5432/tcp": {}},
        }
        check_metadata(source, copy.deepcopy(source))
        # Docker stores the default root directory as either "" or "/".
        explicit_root = copy.deepcopy(source)
        explicit_root["WorkingDir"] = "/"
        check_metadata(source, explicit_root)
        for key, value in (
            ("Entrypoint", ["sh"]), ("StopSignal", "SIGTERM"),
            ("WorkingDir", "/unexpected"),
            ("Volumes", {"/var/lib/postgresql": {}}),
            ("Env", ["PG_MAJOR=17", "PGDATA=/another/path", "LANG=en_US.utf8"]),
        ):
            with self.subTest(key=key):
                target = copy.deepcopy(source)
                target[key] = value
                with self.assertRaises(RuntimeError):
                    check_metadata(source, target)

    def test_accepts_only_documented_server_package_revision(self):
        source = {"Env": ["PG_MAJOR=17", "PG_VERSION=17.11-1.pgdg12+2"]}
        target = {"Env": ["PG_MAJOR=17", "PG_VERSION=17.11-1.pgdg12+2+scoreit1"]}
        check_metadata(source, target)
        with self.assertRaisesRegex(RuntimeError, "environment changed"):
            check_metadata(source, source)


if __name__ == "__main__":
    unittest.main()
