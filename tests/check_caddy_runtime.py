#!/usr/bin/env python3
"""Check the saved Caddy payload, HTTPS trust and persistent certificate storage.

Only disposable containers, an internal network and temporary volumes are used.
Images must already be loaded; no host ports or production mounts are used.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from verify_release import Archive, image_name  # noqa: E402


PAYLOAD = {
    "usr/bin/caddy", "etc/ssl/certs/ca-certificates.crt", "etc/mime.types",
    "etc/passwd", "etc/group", "etc/caddy/Caddyfile", "usr/share/caddy/index.html",
    "usr/share/licenses/caddy/LICENSE", "usr/share/licenses/go/LICENSE",
    "usr/share/zoneinfo/zoneinfo.zip",
    "usr/share/caddy/build-deps.txt",
}
TLS_CLIENT = r'''
import ssl, sys, time, urllib.error, urllib.request
context = ssl.create_default_context(cadata=sys.stdin.read())
url = 'https://caddy-runtime.test/'
deadline = time.monotonic() + 15
while True:
    try:
        urllib.request.urlopen(url, timeout=3)
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            break
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.2)
    else:
        raise AssertionError('Untrusted private certificate was accepted')
with urllib.request.urlopen(url, context=context, timeout=5) as response:
    assert response.status == 200 and response.read() == b'CADDY_TLS_OK'
print('CADDY_HTTPS_OK')
'''
PROXY_CLIENT = r'''
import sys, time, urllib.error, urllib.request
expected = int(sys.argv[1])
deadline = time.monotonic() + 15
while True:
    try:
        response = urllib.request.urlopen('http://caddy-runtime-proxy/', timeout=3)
    except urllib.error.HTTPError as error:
        assert error.code == expected == 502, error.code
        break
    except urllib.error.URLError:
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.2)
    else:
        with response:
            assert response.status == expected == 200
            assert response.read() == b'CADDY_TLS_OK'
        break
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def docker(*args, input=None, timeout=60):
    result = subprocess.run(
        ["docker", *args], stdin=subprocess.DEVNULL if input is None else None,
        input=input, capture_output=True, text=True, timeout=timeout,
    )
    require(result.returncode == 0, f"Docker {args[0]} failed: {result.stderr}")
    return result.stdout.strip()


def layer_names(source, image, platform):
    raw = source.config(image, platform)
    candidates = []
    if "manifest.json" in source.members:
        for row in json.loads(source.read("manifest.json")):
            if (image_name(image) in [image_name(t) for t in row.get("RepoTags") or []]
                    and source.read(row["Config"]) == raw):
                candidates.append(row["Layers"])
    else:
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()

        def walk(descriptor, depth=0):
            require(depth < 9, "OCI index is too deeply nested")
            node = json.loads(source.blob(descriptor))
            if "manifests" in node:
                for child in node["manifests"]:
                    walk(child, depth + 1)
            elif node.get("config", {}).get("digest") == digest:
                names = []
                for layer in node["layers"]:
                    require(re.fullmatch(r"sha256:[a-f0-9]{64}", layer["digest"]),
                            "Unsupported layer digest")
                    names.append("blobs/sha256/" + layer["digest"].split(":")[1])
                candidates.append(names)

        for descriptor in json.loads(source.read("index.json"))["manifests"]:
            walk(descriptor)
    require(len(candidates) == 1, "Expected one saved Caddy image")
    return json.loads(raw), candidates[0]


def check_archive(path, image, platform="linux/amd64"):
    """Inspect every layer, including files later overwritten or deleted."""
    files = {}
    links = []
    roots = b""
    binary = b""
    dependencies = b""
    with Archive(path) as source:
        config, layers = layer_names(source, image, platform)
        env = dict(v.split("=", 1) for v in config["config"].get("Env", []))
        require(env.get("XDG_CONFIG_HOME") == "/config"
                and env.get("XDG_DATA_HOME") == "/data", "Certificate paths changed")
        require(not {"GODEBUG", "OPENSSL_CONF"}.intersection(env),
                "Build-only TLS settings leaked into runtime")
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
                        if item.isdir():
                            continue
                        if item.issym():
                            links.append((name, item.linkname))
                            continue
                        require(item.isfile() and name in PAYLOAD,
                                "Unexpected runtime file: " + name)
                        require(name not in files, "Overwritten file retained in layers: " + name)
                        files[name] = item.size
                        if name in ("usr/bin/caddy", "etc/ssl/certs/ca-certificates.crt",
                                    "usr/share/caddy/build-deps.txt"):
                            with layer.extractfile(item) as content:
                                raw = content.read()
                            if name == "usr/bin/caddy":
                                binary = raw
                            elif name == "usr/share/caddy/build-deps.txt":
                                dependencies = raw
                            else:
                                roots = raw
    require(set(files) == PAYLOAD, "Required runtime data files are missing")
    require(links == [("etc/ssl/cert.pem", "certs/ca-certificates.crt")],
            "Unexpected runtime symlink")
    require(roots.count(b"-----BEGIN CERTIFICATE-----") > 20, "CA bundle is missing")
    deps = dependencies.decode('utf-8').splitlines()
    require(deps and deps[-1] == hashlib.sha256(binary).hexdigest() + '  /out/caddy',
            'Caddy dependency evidence does not match the saved executable')
    require('github.com/caddyserver/caddy/v2' in deps,
            'Caddy dependency evidence is incomplete')
    require(not any(p == 'golang.org/x/crypto/openpgp'
                    or p.startswith('golang.org/x/crypto/openpgp/') for p in deps),
            'OpenPGP is imported by Caddy')
    require(binary[:6] == b"\x7fELF\x02\x01", "Expected a little-endian ELF64 binary")
    offset = struct.unpack_from("<Q", binary, 32)[0]
    size, count = struct.unpack_from("<HH", binary, 54)
    require(size >= 4 and offset + size * count <= len(binary), "Invalid ELF program headers")
    require(all(struct.unpack_from("<I", binary, offset + i * size)[0] != 3
                for i in range(count)), "Caddy requires a dynamic loader")
    return roots


def check_runtime(image, client_image, roots, directory):
    prefix = "score-it-caddy-runtime-" + uuid.uuid4().hex
    network = prefix + "-net"
    volumes = []
    containers = []
    network_created = False
    try:
        docker("network", "create", "--internal", network)
        network_created = True
        for suffix in ("data", "config"):
            volume = prefix + "-" + suffix
            docker("volume", "create", volume)
            volumes.append(volume)
        caddyfile = directory / "Caddyfile"
        caddyfile.write_text('''{
    admin off
    auto_https disable_redirects
    skip_install_trust
}
https://caddy-runtime.test {
    tls internal
    respond "CADDY_TLS_OK"
}
''')
        caddy = docker(
            "create", "--pull=never", "--read-only", "--tmpfs", "/tmp",
            "--name", prefix + "-tls", "--network", network,
            "--network-alias", "caddy-runtime.test",
            "--mount", f"type=bind,source={caddyfile},target=/etc/caddy/Caddyfile,readonly",
            "--mount", f"type=volume,source={volumes[0]},target=/data",
            "--mount", f"type=volume,source={volumes[1]},target=/config", image,
        )
        containers.append(caddy)
        docker("start", caddy)
        root = directory / "root.crt"
        deadline = time.monotonic() + 20
        while True:
            result = subprocess.run(
                ["docker", "cp", caddy + ":/data/caddy/pki/authorities/local/root.crt", str(root)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                break
            require(time.monotonic() < deadline, "Test Caddy did not create a certificate")
            time.sleep(0.2)
        certificate = root.read_text()
        client_args = ["run", "--rm", "--pull=never", "--network", network,
                       "--entrypoint", "python", client_image, "-c"]
        # Input is intentional here: the client receives only the synthetic CA certificate.
        docker("run", "--rm", "-i", "--pull=never", "--network", network,
               "--entrypoint", "python", client_image, "-c", TLS_CLIENT, input=certificate)
        print("CADDY_HTTPS_OK", flush=True)
        docker("restart", "--time", "2", caddy)
        docker("cp", caddy + ":/data/caddy/pki/authorities/local/root.crt", str(root))
        require(root.read_text() == certificate, "Certificate storage did not persist")
        docker("run", "--rm", "-i", "--pull=never", "--network", network,
               "--entrypoint", "python", client_image, "-c", TLS_CLIENT, input=certificate)
        docker("cp", caddy + ":/config/caddy/autosave.json", str(directory / "autosave.json"))
        print("CADDY_STORAGE_OK", flush=True)

        proxyfile = directory / "Proxyfile"
        proxyfile.write_text('''{
    admin off
}
http://caddy-runtime-proxy {
    reverse_proxy https://caddy-runtime.test {
        header_up Host caddy-runtime.test
    }
}
''')
        bundle = directory / "roots.crt"
        bundle.write_bytes(roots + b"\n" + certificate.encode())
        for trusted in (False, True):
            mounts = ["--mount", f"type=bind,source={proxyfile},target=/etc/caddy/Caddyfile,readonly"]
            if trusted:
                mounts.extend(["--mount", f"type=bind,source={bundle},target=/etc/ssl/certs/ca-certificates.crt,readonly"])
            proxy = docker(
                "create", "--pull=never", "--read-only", "--tmpfs", "/tmp",
                "--tmpfs", "/config", "--tmpfs", "/data",
                "--network", network, "--network-alias", "caddy-runtime-proxy",
                *mounts, image,
            )
            containers.append(proxy)
            docker("start", proxy)
            expected = "200" if trusted else "502"
            docker(*client_args, PROXY_CLIENT, expected)
            docker("rm", "-fv", proxy)
            containers.remove(proxy)
        print("CADDY_UPSTREAM_TLS_TRUST_OK", flush=True)
    finally:
        for container in reversed(containers):
            subprocess.run(["docker", "rm", "-fv", container], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=30)
        for volume in reversed(volumes):
            subprocess.run(["docker", "volume", "rm", volume], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=30)
        if network_created:
            subprocess.run(["docker", "network", "rm", network], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--client-image", required=True, help="Loaded image with Python 3")
    args = parser.parse_args()
    info = json.loads(docker("image", "inspect", args.image))[0]
    docker("image", "inspect", args.client_image)
    output = docker("run", "--rm", "--pull=never", "--network", "none",
                    "--entrypoint", "caddy", info["Id"], "build-info")
    require(re.search(r"^go\s+go1[.]26[.]10$", output, re.M), "Unexpected Go version")
    require(re.search(r"^dep\s+golang.org/x/net\s+v0[.]60[.]0\s", output, re.M),
            "Unexpected x/net version")
    with tempfile.TemporaryDirectory(prefix="score-it-caddy-runtime-") as temp:
        directory = Path(temp)
        path = directory / "caddy.tar"
        docker("image", "save", "--output", str(path), args.image, timeout=120)
        roots = check_archive(path, args.image)
        require(json.loads(docker("image", "inspect", args.image))[0]["Id"] == info["Id"],
                "Caddy tag changed during export")
        print("CADDY_ARCHIVE_PAYLOAD_OK", flush=True)
        print("CADDY_OPENPGP_NOT_IMPORTED_OK", flush=True)
        check_runtime(info["Id"], args.client_image, roots, directory)
    print("CADDY_RUNTIME_OK", flush=True)


if __name__ == "__main__":
    main()
