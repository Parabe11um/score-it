"""Regression checks for obsolete Caddy/OS files hidden in saved image layers."""

import hashlib
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest

from check_caddy_runtime import PAYLOAD, check_archive


IMAGE = "score-it-caddy:payload-test"


def binary(interpreter=False):
    data = bytearray(120)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 1)
    struct.pack_into("<I", data, 64, 3 if interpreter else 1)
    return bytes(data)


def payload(interpreter=False):
    values = {name: b"data" for name in PAYLOAD}
    values["usr/bin/caddy"] = binary(interpreter)
    values['usr/share/caddy/build-deps.txt'] = (
        'github.com/caddyserver/caddy/v2\n' + hashlib.sha256(values['usr/bin/caddy']).hexdigest()
        + '  /out/caddy\n').encode()
    values["etc/ssl/certs/ca-certificates.crt"] = b"-----BEGIN CERTIFICATE-----\n" * 21
    return values


def layer_bytes(files, link=True):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as layer:
        for name, data in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mode = 0o755 if name in ("usr/bin/caddy", "usr/local/bin/gosu") else 0o644
            layer.addfile(item, io.BytesIO(data))
        if link:
            item = tarfile.TarInfo("etc/ssl/cert.pem")
            item.type = tarfile.SYMTYPE
            item.linkname = "certs/ca-certificates.crt"
            layer.addfile(item)
    return stream.getvalue()


def saved_image(path, layers, oci=False, image=IMAGE):
    config = json.dumps({
        "os": "linux", "architecture": "amd64",
        "config": {"Env": ["XDG_CONFIG_HOME=/config", "XDG_DATA_HOME=/data"]},
        "rootfs": {"type": "layers", "diff_ids": [
            "sha256:" + hashlib.sha256(layer).hexdigest() for layer in layers]},
    }).encode()
    with tarfile.open(path, "w") as archive:
        def add(name, data):
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))

        if not oci:
            add("config.json", config)
            names = []
            for number, layer in enumerate(layers):
                name = f"{number}/layer.tar"
                names.append(name)
                add(name, layer)
            add("manifest.json", json.dumps([{
                "Config": "config.json", "RepoTags": [image], "Layers": names,
            }]).encode())
        else:
            def blob(data):
                digest = hashlib.sha256(data).hexdigest()
                add("blobs/sha256/" + digest, data)
                return {"digest": "sha256:" + digest, "size": len(data)}

            manifest = json.dumps({"config": blob(config), "layers": [blob(x) for x in layers]}).encode()
            descriptor = blob(manifest)
            descriptor["annotations"] = {"org.opencontainers.image.ref.name": image}
            add("index.json", json.dumps({"manifests": [descriptor]}).encode())


class CaddyPayloadTests(unittest.TestCase):
    def test_rejects_openpgp_imports_and_unbound_evidence(self):
        for dependency in ('golang.org/x/crypto/openpgp', 'golang.org/x/crypto/openpgp/packet'):
            data = payload()
            data['usr/share/caddy/build-deps.txt'] = dependency.encode() + b'\n' + data['usr/share/caddy/build-deps.txt']
            with self.assertRaisesRegex(RuntimeError, 'OpenPGP is imported'):
                self.verify([layer_bytes(data)])
        data = payload()
        data['usr/share/caddy/build-deps.txt'] = b'wrong binary hash'
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            self.verify([layer_bytes(data)])

    def verify(self, layers, oci=False):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "caddy.tar"
            saved_image(path, layers, oci=oci)
            return check_archive(path, IMAGE)

    def test_accepts_classic_and_oci_payloads(self):
        for oci in (False, True):
            with self.subTest(oci=oci):
                self.assertIn(b"BEGIN CERTIFICATE", self.verify([layer_bytes(payload())], oci))

    def test_rejects_original_binary_under_a_new_binary(self):
        for oci in (False, True):
            with self.subTest(oci=oci):
                with self.assertRaisesRegex(RuntimeError, "Overwritten file retained"):
                    self.verify([layer_bytes({"usr/bin/caddy": b"obsolete"}, link=False),
                                 layer_bytes(payload())], oci)

    def test_rejects_busybox_even_if_a_later_layer_deletes_it(self):
        with self.assertRaisesRegex(RuntimeError, "Unexpected runtime file"):
            self.verify([layer_bytes({"bin/busybox": b"old"}, link=False),
                         layer_bytes({"bin/.wh.busybox": b""}, link=False),
                         layer_bytes(payload())])

    def test_rejects_nghttp2(self):
        values = payload()
        values["usr/lib/libnghttp2.so.14"] = b"old"
        with self.assertRaisesRegex(RuntimeError, "Unexpected runtime file"):
            self.verify([layer_bytes(values)])

    def test_rejects_dynamic_loader_dependency(self):
        with self.assertRaisesRegex(RuntimeError, "dynamic loader"):
            self.verify([layer_bytes(payload(interpreter=True))])

    def test_rejects_missing_ca_store(self):
        values = payload()
        del values["etc/ssl/certs/ca-certificates.crt"]
        with self.assertRaisesRegex(RuntimeError, "Required runtime data files"):
            self.verify([layer_bytes(values)])


if __name__ == "__main__":
    unittest.main()
