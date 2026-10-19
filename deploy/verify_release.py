"""Verify saved image configs across classic Docker and containerd stores.

Uses Python 3's standard library and Docker CLI only. Never runs containers or
downloads images. Temporary exports are deleted after verification.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from tempfile import TemporaryDirectory


class VerificationError(Exception):
    pass


def file_digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return "sha256:" + sha.hexdigest()


def image_name(name):
    for prefix in ("docker.io/", "index.docker.io/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.removeprefix("library/")


def platform_matches(config, platform):
    wanted = platform.split("/")
    return (
        len(wanted) in (2, 3)
        and [config.get("os"), config.get("architecture")] == wanted[:2]
        and (len(wanted) == 2 or config.get("variant") == wanted[2])
    )


class Archive:
    def __init__(self, path):
        self.tar = tarfile.open(path, "r:*")
        self.members = {}
        for member in self.tar.getmembers():
            name = str(PurePosixPath(member.name))
            if name in self.members:
                self.tar.close()
                raise VerificationError("Duplicate archive member: " + name)
            self.members[name] = member

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.tar.close()

    def read(self, name):
        member = self.members.get(str(PurePosixPath(name)))
        if member is None or not member.isfile() or member.size > 8 * 1024 * 1024:
            raise VerificationError("Missing or oversized image metadata: " + name)
        with self.tar.extractfile(member) as stream:
            return stream.read()

    def blob(self, descriptor):
        digest = descriptor["digest"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise VerificationError("Unsupported OCI digest")
        raw = self.read("blobs/sha256/" + digest.removeprefix("sha256:"))
        if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            raise VerificationError("OCI metadata digest mismatch")
        return raw

    def config(self, tag, platform):
        candidates = []
        if "manifest.json" in self.members:
            for manifest in json.loads(self.read("manifest.json")):
                if image_name(tag) in [image_name(value) for value in manifest.get("RepoTags") or []]:
                    raw = self.read(manifest["Config"])
                    if platform_matches(json.loads(raw), platform):
                        candidates.append(raw)
        elif "index.json" in self.members:
            def walk(descriptor, names=(), depth=0):
                if depth > 8:
                    raise VerificationError("OCI index nesting is too deep")
                annotations = descriptor.get("annotations") or {}
                names = (*names, *annotations.values())
                node = json.loads(self.blob(descriptor))
                if "manifests" in node:
                    for child in node["manifests"]:
                        walk(child, names, depth + 1)
                elif "config" in node and image_name(tag) in [image_name(value) for value in names]:
                    raw = self.blob(node["config"])
                    if platform_matches(json.loads(raw), platform):
                        candidates.append(raw)

            for descriptor in json.loads(self.read("index.json"))["manifests"]:
                walk(descriptor)
        else:
            raise VerificationError("Archive has no Docker manifest or OCI index")
        if len(candidates) != 1:
            raise VerificationError(f"Expected one image config for {tag} ({platform}); found {len(candidates)}")
        return candidates[0]


def verify_checksums(directory):
    checked = set()
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", digest) or name in checked:
            raise VerificationError("Invalid checksum manifest")
        if file_digest(directory / name) != "sha256:" + digest:
            raise VerificationError("Checksum mismatch: " + name)
        checked.add(name)
    if not {"images.tar", "images.tsv", "release.env", "VERSION", "compose.production.yaml"} <= checked:
        raise VerificationError("Release files are missing from checksum manifest")


def manifest_rows(path):
    with Path(path).open(newline="") as stream:
        rows = list(csv.reader(stream, delimiter="\t"))
    if len(rows) != 2 or any(len(row) != 3 for row in rows):
        raise VerificationError("Expected exactly two images in images.tsv")
    if len({row[0] for row in rows}) != len(rows):
        raise VerificationError("Duplicate image tags")
    kinds = set()
    for tag, digest, platform in rows:
        if tag.startswith("score-it:"):
            kinds.add("app")
        elif tag.startswith("score-it-postgres:"):
            kinds.add("db")
        else:
            raise VerificationError("Unexpected image tag")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise VerificationError("Invalid config digest")
        if not re.fullmatch(r"linux/[a-z0-9_]+(?:/[a-z0-9_]+)?", platform):
            raise VerificationError("Invalid platform")
    if kinds != {"app", "db"}:
        raise VerificationError("Application and PostgreSQL images are required")
    return rows


def inspect(tag):
    result = subprocess.check_output(["docker", "image", "inspect", tag], text=True)
    values = json.loads(result)
    if len(values) != 1:
        raise VerificationError("Image inspection is ambiguous")
    return values[0]


def verify_loaded_image(source, tag, digest, platform):
    expected_raw = source.config(tag, platform)
    if "sha256:" + hashlib.sha256(expected_raw).hexdigest() != digest:
        raise VerificationError("Saved image config does not match images.tsv: " + tag)
    expected = json.loads(expected_raw)
    before = inspect(tag)
    if not platform_matches({"os": before.get("Os"), "architecture": before.get("Architecture"),
                             "variant": before.get("Variant")}, platform):
        raise VerificationError("Loaded platform differs: " + tag)
    layers = (before.get("RootFS") or {}).get("Layers", [])
    if layers != expected["rootfs"]["diff_ids"]:
        raise VerificationError("Loaded layer digests differ: " + tag)
    # Image IDs describe config digests in classic storage, but manifest/index
    # digests in containerd. Export the selected tag and check the original raw
    # config instead; its digest includes runtime configuration and layer IDs.
    with TemporaryDirectory(prefix="score-it-verify-") as directory:
        path = Path(directory) / "loaded.tar"
        subprocess.run(["docker", "image", "save", "--output", str(path), tag], check=True)
        with Archive(path) as loaded:
            actual_raw = loaded.config(tag, platform)
        if actual_raw != expected_raw:
            raise VerificationError("Loaded image config differs: " + tag)
    if inspect(tag)["Id"] != before["Id"]:
        raise VerificationError("Image tag changed during verification: " + tag)


def verify_release(directory):
    directory = Path(directory)
    verify_checksums(directory)
    print("CHECKSUMS_OK", flush=True)
    rows = manifest_rows(directory / "images.tsv")
    with Archive(directory / "images.tar") as source:
        for tag, digest, platform in rows:
            verify_loaded_image(source, tag, digest, platform)
            print(f"IMAGE_VERIFIED {tag} {platform} {digest}", flush=True)
    print("IMAGES_VERIFIED", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--release-dir", default=".")
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--archive", required=True)
    manifest.add_argument("--output", required=True)
    manifest.add_argument("--platform", required=True)
    manifest.add_argument("--image", action="append", required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            verify_release(args.release_dir)
        else:
            rows = []
            with Archive(args.archive) as archive:
                for tag in args.image:
                    raw = archive.config(tag, args.platform)
                    rows.append((tag, "sha256:" + hashlib.sha256(raw).hexdigest(), args.platform))
            with Path(args.output).open("x", newline="") as stream:
                csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(rows)
    except (VerificationError, OSError, ValueError, KeyError, TypeError, tarfile.TarError,
            subprocess.CalledProcessError) as error:
        parser.exit(1, f"Verification stopped: {error}\n")


if __name__ == "__main__":
    main()
