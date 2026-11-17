#!/usr/bin/env python3
"""Fail release verification on CRITICAL findings that still apply.

Keep Trivy reports intact. The sole reviewed decision is about the Debian
Bookworm zlib1g binary package, not every occurrence of the MiniZip CVE.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

from check_caddy_runtime import Archive, layer_names, require
from verify_release import VerificationError, manifest_rows


MINIZIP_CVE = "CVE-2023-45853"
BOOKWORM_ZLIB = "1:1.2.13.dfsg-1"
DEBIAN_TRACKER = "https://security-tracker.debian.org/tracker/" + MINIZIP_CVE
CHUNK = 1024 * 1024
MARKERS = (b"minizip", b"zipopennewfileinzip", b"unzopen2_64", b"mz_zip_writer_open")
ZLIB_PATHS = {
    PurePosixPath(prefix, "x86_64-linux-gnu/libz.so.1.2.13")
    for prefix in ("lib", "usr/lib")
}
ROLES = {"score-it": "app", "score-it-postgres": "postgres", "score-it-caddy": "caddy"}


def sha256(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


class LayerReader:
    """Hash the uncompressed tar stream, including bytes past its end marker."""

    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size):
        block = self.stream.read(size)
        self.digest.update(block)
        return block

    def verify(self, diff_id):
        while self.read(CHUNK):
            pass
        require("sha256:" + self.digest.hexdigest() == diff_id, "Image layer digest mismatch")


def inspect_layer(layer, evidence):
    for member in layer:
        path = PurePosixPath(member.name)
        require(not path.is_absolute() and ".." not in path.parts, "Unsafe layer member path")
        # Inspect every occurrence; a later whiteout cannot hide an earlier file.
        require("minizip" not in str(path).lower() and "minizip" not in member.linkname.lower(),
                "MiniZip-related file or link in saved layer: " + str(path))
        if path in ZLIB_PATHS:
            require(member.isfile(), "Expected the Debian libz shared library to be a regular file")
        if not member.isfile():
            continue

        with layer.extractfile(member) as stream:
            if str(path) in {"etc/os-release", "usr/lib/os-release", "var/lib/dpkg/status"}:
                require(member.size <= 8 * CHUNK, "Oversized Debian metadata")
                data = stream.read()
                if path.name == "os-release":
                    values = dict(line.split(b"=", 1) for line in data.splitlines()
                                  if b"=" in line and not line.startswith(b"#"))
                    require(values.get(b"ID", b"").strip(b'"') == b"debian"
                            and values.get(b"VERSION_CODENAME", b"").strip(b'"') == b"bookworm",
                            "Expected Debian Bookworm in the saved layer")
                    evidence["distribution"] = "debian/bookworm"
                else:
                    for stanza in data.split(b"\n\n"):
                        fields = dict(line.split(b": ", 1) for line in stanza.splitlines()
                                      if b": " in line and not line.startswith((b" ", b"\t")))
                        package = fields.get(b"Package", b"")
                        require(b"minizip" not in package.lower(), "MiniZip in the saved package inventory")
                        if package == b"zlib1g" and fields.get(b"Status") == b"install ok installed":
                            require(fields.get(b"Version") == BOOKWORM_ZLIB.encode()
                                    and fields.get(b"Architecture") == b"amd64",
                                    "Saved zlib1g package version or architecture differs")
                            evidence["zlib1g"] = BOOKWORM_ZLIB
                continue

            magic = stream.read(4)
            if path in ZLIB_PATHS:
                require(magic == b"\x7fELF", "The saved Debian libz file is not ELF")
            if magic != b"\x7fELF":
                continue
            overlap = b""
            digest = hashlib.sha256(magic) if path in ZLIB_PATHS else None
            for block in iter(lambda: stream.read(CHUNK), b""):
                window = overlap + block.lower()
                require(not any(marker in window for marker in MARKERS),
                        "MiniZip symbol or library marker in saved ELF: " + str(path))
                overlap = window[-(max(map(len, MARKERS)) - 1):]
                if digest is not None:
                    digest.update(block)
            if digest is not None:
                evidence.setdefault("libz_files", {})[str(path)] = "sha256:" + digest.hexdigest()


def prove_bookworm_zlib(source, image, platform, config_id):
    require(platform == "linux/amd64" and sha256(source.config(image, platform)) == config_id,
            "PostgreSQL config does not match the reviewed image")
    config, layers = layer_names(source, image, platform)
    diffs = config["rootfs"]["diff_ids"]
    require(len(layers) == len(diffs) and layers, "Missing or mismatched PostgreSQL layers")
    evidence = {"layer_diff_ids": diffs, "vendor_advisory": DEBIAN_TRACKER}
    for name, diff_id in zip(layers, diffs):
        member = source.members.get(str(PurePosixPath(name)))
        require(member is not None and member.isfile(), "Missing image layer")
        with source.tar.extractfile(member) as raw:
            magic = raw.read(4)
            raw.seek(0)
            require(magic != b"\x28\xb5\x2f\xfd", "Zstd layer verification is not supported")
            if magic.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=raw) as stream:
                    check_layer(stream, diff_id, evidence)
            else:
                check_layer(raw, diff_id, evidence)
    require({"distribution", "zlib1g", "libz_files"} <= evidence.keys(),
            "Incomplete Bookworm zlib evidence")
    return evidence


def check_layer(stream, diff_id, evidence):
    reader = LayerReader(stream)
    with tarfile.open(fileobj=reader, mode="r|") as layer:
        inspect_layer(layer, evidence)
    reader.verify(diff_id)


def read_critical(report, role, platform, config_id):
    metadata = report["Metadata"]
    require(report["SchemaVersion"] == 2 and metadata.get("ImageID") == config_id,
            "Trivy report schema or image identity differs: " + role)
    results = report["Results"]
    require(isinstance(results, list) and results, "Missing Trivy results: " + role)
    inventory = ("lang-pkgs", "gobinary") if role == "caddy" else ("os-pkgs", "debian")
    require(any((result.get("Class"), result.get("Type")) == inventory for result in results),
            "Trivy did not report the expected scan target: " + role)
    os_info = metadata.get("OS") or {}

    critical = []
    for result in results:
        vulnerabilities = result.get("Vulnerabilities")
        require(vulnerabilities is None or isinstance(vulnerabilities, list),
                "Invalid Trivy vulnerability records: " + role)
        for item in vulnerabilities or []:
            if item["Severity"].upper() != "CRITICAL":
                continue
            # Every other CRITICAL is blocking, including the CVE on MiniZip
            # itself, a new package version, or a different distribution.
            candidate = (
                role == "postgres" and platform == "linux/amd64"
                and (result.get("Class"), result.get("Type")) == ("os-pkgs", "debian")
                and os_info.get("Family") == "debian" and os_info.get("Name", "").split(".")[0] == "12"
                and item["VulnerabilityID"] == MINIZIP_CVE and item["PkgName"] == "zlib1g"
                and item["InstalledVersion"] == BOOKWORM_ZLIB and not item.get("FixedVersion")
            )
            critical.append({
                "id": item["VulnerabilityID"], "package": item["PkgName"],
                "installed_version": item["InstalledVersion"], "fixed_version": item.get("FixedVersion", ""),
                "severity_source": item.get("SeveritySource"), "target": result.get("Target"),
                "decision": "pending_proof" if candidate else "unresolved",
            })
    return critical


def review(archive, manifest, report_dir):
    images = []
    with Archive(archive) as source:
        for tag, config_id, platform in manifest_rows(manifest):
            role = ROLES[tag.split(":", 1)[0]]
            require(sha256(source.config(tag, platform)) == config_id,
                    "Archive and images.tsv disagree: " + role)
            raw = (report_dir / (role + ".json")).read_bytes()
            critical = read_critical(json.loads(raw), role, platform, config_id)
            image = {"role": role, "image": tag, "config_id": config_id, "platform": platform,
                     "trivy_report_sha256": sha256(raw), "findings": critical}
            if any(item["decision"] == "pending_proof" for item in critical):
                try:
                    image["minizip_evidence"] = prove_bookworm_zlib(source, tag, platform, config_id)
                except (VerificationError, RuntimeError, OSError, EOFError, tarfile.TarError) as error:
                    image["minizip_evidence_error"] = str(error)
                for item in critical:
                    if item["decision"] == "pending_proof":
                        item["decision"] = "not_affected" if "minizip_evidence" in image else "unresolved"
            images.append(image)
    return {"images": images, "unresolved_critical_records": sum(
        finding["decision"] == "unresolved" for image in images for finding in image["findings"]
    )}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("archive", "manifest", "report-dir", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()

    report = review(args.archive, args.manifest, args.report_dir)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for image in report["images"]:
        findings = image["findings"]
        unresolved = [item for item in findings if item["decision"] == "unresolved"]
        print(f"{image['role']}: raw_critical_records={len(findings)} "
              f"unresolved_critical_records={len(unresolved)}")
        for item in unresolved:
            print(f"  {item['id']} {item['package']} {item['installed_version']} "
                  f"fixed={item['fixed_version'] or 'not reported'}")
        if "minizip_evidence_error" in image:
            print("  MiniZip evidence failed: " + image["minizip_evidence_error"])
    if report["unresolved_critical_records"]:
        raise SystemExit("RELEASE_BLOCKED: unresolved CRITICAL findings; see critical.json.")
    print("CRITICAL_GATE_OK")


if __name__ == "__main__":
    main()
