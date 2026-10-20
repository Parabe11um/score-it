import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import tarfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("verify_release", Path(__file__).resolve().parents[1] / "deploy/verify_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

TAG = "score-it:compat"
CONFIG = {"os": "linux", "architecture": "amd64", "config": {"Cmd": ["/app"], "Labels": {"version": "1"}},
          "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64]}}
RAW = json.dumps(CONFIG).encode()
DIGEST = "sha256:" + hashlib.sha256(RAW).hexdigest()


def write_archive(path, raw=RAW, platform="linux/amd64", duplicate=False, oci=False):
    members = {}
    if oci:
        config_name = "blobs/sha256/" + hashlib.sha256(raw).hexdigest()
        manifest = json.dumps({"schemaVersion": 2, "config": {"digest": "sha256:" + hashlib.sha256(raw).hexdigest()}, "layers": []}).encode()
        manifest_digest = hashlib.sha256(manifest).hexdigest()
        members["index.json"] = json.dumps({"manifests": [{"digest": "sha256:" + manifest_digest,
            "annotations": {"io.containerd.image.name": "docker.io/library/" + TAG}}]}).encode()
        members["blobs/sha256/" + manifest_digest] = manifest
        members[config_name] = raw
    else:
        manifest = {"Config": "config.json", "RepoTags": ["docker.io/library/" + TAG], "Layers": []}
        members["manifest.json"] = json.dumps([manifest, manifest] if duplicate else [manifest]).encode()
        members["config.json"] = raw
    with tarfile.open(path, "w") as archive:
        for name, value in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, BytesIO(value))


class ReleaseVerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "source.tar"
        write_archive(self.path)

    def inspect_result(self, image_id):
        return {"Id": image_id, "Os": "linux", "Architecture": "amd64", "RootFS": {"Layers": CONFIG["rootfs"]["diff_ids"]}}

    def verify(self, loaded_raw=RAW, actual=None, after=None):
        def save(arguments, check):
            write_archive(Path(arguments[4]), loaded_raw)
            return subprocess.CompletedProcess(arguments, 0)
        actual = actual or self.inspect_result("sha256:containerd-manifest")
        with release.Archive(self.path) as source, patch.object(release, "inspect", side_effect=[actual, after or actual]), \
             patch.object(release.subprocess, "run", side_effect=save):
            release.verify_loaded_image(source, TAG, DIGEST, "linux/amd64")

    def test_changed_docker_id_does_not_reject_identical_config(self):
        self.verify()

    def test_classic_id_is_also_accepted(self):
        self.verify(actual=self.inspect_result(DIGEST))

    def test_different_command_with_same_layers_is_rejected(self):
        changed = dict(CONFIG, config={"Cmd": ["/wrong"]})
        with self.assertRaisesRegex(release.VerificationError, "config differs"):
            self.verify(loaded_raw=json.dumps(changed).encode())

    def test_different_layers_are_rejected_before_export(self):
        value = self.inspect_result("sha256:manifest")
        value["RootFS"]["Layers"] = ["sha256:" + "b" * 64]
        with self.assertRaisesRegex(release.VerificationError, "layer digests"):
            self.verify(actual=value)

    def test_wrong_platform_is_rejected(self):
        value = dict(self.inspect_result("sha256:manifest"), Architecture="arm64")
        with self.assertRaisesRegex(release.VerificationError, "platform"):
            self.verify(actual=value)

    def test_tag_change_during_export_is_rejected(self):
        with self.assertRaisesRegex(release.VerificationError, "tag changed"):
            self.verify(after=self.inspect_result("sha256:changed"))

    def test_saved_config_digest_must_match_release_manifest(self):
        with release.Archive(self.path) as source:
            with self.assertRaisesRegex(release.VerificationError, "images.tsv"):
                release.verify_loaded_image(source, TAG, "sha256:" + "0" * 64, "linux/amd64")

    def test_ambiguous_manifests_are_rejected(self):
        write_archive(self.path, duplicate=True)
        with release.Archive(self.path) as source:
            with self.assertRaises(release.VerificationError):
                source.config(TAG, "linux/amd64")

    def test_oci_layout_without_compatibility_manifest_is_supported(self):
        write_archive(self.path, oci=True)
        with release.Archive(self.path) as source:
            self.assertEqual(source.config(TAG, "linux/amd64"), RAW)

    def test_changed_release_checksum_is_rejected(self):
        root = Path(self.directory.name)
        (root / "SHA256SUMS").write_text("0" * 64 + "  source.tar\n")
        with self.assertRaisesRegex(release.VerificationError, "Checksum mismatch"):
            release.verify_checksums(root)

    def test_security_subdirectory_is_verified_and_cannot_escape_release(self):
        root = Path(self.directory.name)
        names = ['images.tar', 'images.tsv', 'release.env', 'VERSION', 'compose.production.yaml', 'security/app.json']
        lines = []
        for name in names:
            path = root / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'checked')
            lines.append(hashlib.sha256(b'checked').hexdigest() + '  ' + name)
        (root / 'SHA256SUMS').write_text('\n'.join(lines))
        release.verify_checksums(root)
        (root / 'security/app.json').write_bytes(b'tampered')
        with self.assertRaisesRegex(release.VerificationError, 'Checksum mismatch'):
            release.verify_checksums(root)
        (root / 'SHA256SUMS').write_text(hashlib.sha256(b'checked').hexdigest() + '  ../outside')
        with self.assertRaisesRegex(release.VerificationError, 'Invalid checksum'):
            release.verify_checksums(root)

    def test_three_image_manifest_and_legacy_two_image_manifest_are_supported(self):
        root = Path(self.directory.name)
        tags = ['score-it:release', 'score-it-postgres:release', 'score-it-caddy:release']
        path = root / 'images.tsv'
        for count in (2, 3):
            path.write_text(''.join(f'{tag}\t{DIGEST}\tlinux/amd64\n' for tag in tags[:count]))
            self.assertEqual(len(release.manifest_rows(path)), count)
        path.write_text(''.join(f'{tag}\t{DIGEST}\tlinux/amd64\n' for tag in [tags[0], tags[1], 'score-it:another']))
        with self.assertRaises(release.VerificationError):
            release.manifest_rows(path)


if __name__ == "__main__":
    unittest.main()
