"""The only non-applicability decision requires evidence from the exact archive."""

import gzip
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from check_release_critical import (
    BOOKWORM_ZLIB, MINIZIP_CVE, PATCHED_BOOKWORM_ZLIB, ROLES,
    ZLIB_BACKPORT, ZLIB_RECEIPT, review, sha256,
)
from test_caddy_payload import layer_bytes


PLATFORM = "linux/amd64"
BOOKWORM_FILES = {
    "usr/lib/os-release": b'ID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\n',
    "var/lib/dpkg/status": (
        f"Package: zlib1g\nStatus: install ok installed\nArchitecture: amd64\nVersion: {BOOKWORM_ZLIB}\n"
    ).encode(),
    "usr/lib/x86_64-linux-gnu/libz.so.1.2.13": b"\x7fELFzlib",
}
ZLIB_FINDING = {
    "Severity": "CRITICAL", "VulnerabilityID": MINIZIP_CVE, "PkgName": "zlib1g",
    "InstalledVersion": BOOKWORM_ZLIB, "SeveritySource": "nvd",
}


class CriticalGateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive = self.root / "images.tar"
        self.manifest = self.root / "images.tsv"

    def save(self, postgres_findings=(ZLIB_FINDING,), app_findings=(), caddy_findings=(),
             additional_layers=(), corrupt_layer=False, postgres_files=None):
        pg_files = BOOKWORM_FILES if postgres_files is None else postgres_files
        rows, manifest = [], []
        with tarfile.open(self.archive, "w") as archive:
            def add(name, data):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

            for prefix, role in ROLES.items():
                is_postgres = role == "postgres"
                tag = prefix + ":unit-test"
                layers = [layer_bytes(pg_files if is_postgres else {}, link=False)]
                if is_postgres:
                    layers.extend(layer_bytes(files, link=False) for files in additional_layers)
                config = json.dumps({"os": "linux", "architecture": "amd64",
                                     "rootfs": {"diff_ids": [sha256(layer) for layer in layers]}}).encode()
                config_id = sha256(config)
                rows.append(f"{tag}\t{config_id}\t{PLATFORM}\n")
                config_name = role + "/config.json"
                add(config_name, config)
                names = []
                for i, layer in enumerate(layers):
                    name = f"{role}/{i}/layer.tar"
                    names.append(name)
                    if is_postgres:
                        if corrupt_layer and i == 0:
                            layer += bytes(512)
                        layer = gzip.compress(layer)
                    add(name, layer)
                manifest.append({"RepoTags": [tag], "Config": config_name, "Layers": names})
                findings = {"app": app_findings, "postgres": postgres_findings, "caddy": caddy_findings}[role]
                kind = {"Class": "lang-pkgs", "Type": "gobinary"} if role == "caddy" else {
                    "Class": "os-pkgs", "Type": "debian"}
                (self.root / (role + ".json")).write_text(json.dumps({
                    "SchemaVersion": 2, "Metadata": {"ImageID": config_id, "OS": {"Family": "debian", "Name": "12.13"}},
                    "Results": [dict(kind, Vulnerabilities=list(findings))],
                }))
            add("manifest.json", json.dumps(manifest).encode())
        self.manifest.write_text("".join(rows))

    def review(self, require_zlib_backport=False):
        return review(self.archive, self.manifest, self.root, require_zlib_backport)

    def test_backport_proof_requires_matching_library_in_archive(self):
        path = "usr/lib/x86_64-linux-gnu/libz.so.1.2.13"
        expected = dict(ZLIB_BACKPORT, lib_sha256=sha256(BOOKWORM_FILES[path]).removeprefix("sha256:"))
        files = dict(BOOKWORM_FILES)
        files["var/lib/dpkg/status"] = files["var/lib/dpkg/status"].replace(
            BOOKWORM_ZLIB.encode(), PATCHED_BOOKWORM_ZLIB.encode())
        finding = dict(ZLIB_FINDING, InstalledVersion=PATCHED_BOOKWORM_ZLIB)
        for fields, accepted in ((expected, True),
                                 (dict(expected, lib_sha256="0" * 64), False),
                                 (dict(expected, stock="wbT:0,wb0:0"), False)):
            with self.subTest(accepted=accepted, receipt=fields):
                files[str(ZLIB_RECEIPT)] = "".join(f"{key}={value}\n" for key, value in fields.items()).encode()
                self.save(postgres_files=files, postgres_findings=(finding,))
                review = self.review(require_zlib_backport=True)
                pg = next(image for image in review["images"] if image["role"] == "postgres")
                self.assertEqual(review["unresolved_critical_records"], int(not accepted))
                self.assertEqual("minizip_evidence_error" in pg, not accepted)

    def test_old_library_is_not_accepted_even_without_a_scanner_finding(self):
        self.save(postgres_findings=())
        pg = next(image for image in self.review(require_zlib_backport=True)["images"]
                  if image["role"] == "postgres")
        self.assertIn("backport is missing", pg["minizip_evidence_error"])

    def test_bookworm_decision_keeps_raw_report_and_binds_evidence(self):
        self.save()
        raw = (self.root / "postgres.json").read_bytes()
        result = self.review()
        pg = next(image for image in result["images"] if image["role"] == "postgres")
        self.assertEqual(pg["findings"][0]["decision"], "not_affected")
        self.assertEqual(pg["minizip_evidence"]["zlib1g"], BOOKWORM_ZLIB)
        self.assertEqual(pg["trivy_report_sha256"], sha256(raw))
        self.assertEqual((self.root / "postgres.json").read_bytes(), raw)
        self.assertEqual(result["unresolved_critical_records"], 0)

    def test_cve_in_other_packages_and_any_unreviewed_critical_block(self):
        self.save(
            postgres_findings=(ZLIB_FINDING, dict(ZLIB_FINDING, PkgName="libminizip1"),
                               dict(ZLIB_FINDING, InstalledVersion=BOOKWORM_ZLIB + "+local"),
                               dict(ZLIB_FINDING, FixedVersion="1:1.2.13.dfsg-1+deb12u1")),
            app_findings=(ZLIB_FINDING,),
            caddy_findings=(dict(ZLIB_FINDING, PkgName="example.org/test", VulnerabilityID="CVE-2099-0001"),),
        )
        self.assertEqual(self.review()["unresolved_critical_records"], 5)

    def test_saved_mini_zip_in_earlier_layer_cannot_be_hidden(self):
        for name, payload in (("usr/share/doc/minizip/copyright", b"data"),
                              ("usr/local/bin/custom-zip", b"\x7fELFzipOpenNewFileInZip4_64")):
            with self.subTest(name=name):
                path = Path(name)
                self.save(additional_layers=({name: payload}, {str(path.parent / (".wh." + path.name)): b""}))
                self.assertEqual(self.review()["unresolved_critical_records"], 1)

    def test_wrong_report_or_modified_layer_never_pass(self):
        self.save(corrupt_layer=True)
        pg = next(image for image in self.review()["images"] if image["role"] == "postgres")
        self.assertIn("Image layer digest mismatch", pg["minizip_evidence_error"])
        self.assertEqual(pg["findings"][0]["decision"], "unresolved")

        report_path = self.root / "app.json"
        raw = json.loads(report_path.read_bytes())
        raw["Metadata"]["ImageID"] = "sha256:" + "0" * 64
        report_path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(RuntimeError, "image identity"):
            self.review()
