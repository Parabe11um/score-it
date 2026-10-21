#!/usr/bin/env python3
"""Scan reachable Git history or every saved image layer with pinned Gitleaks.

No image is run, no archive symlink is followed, and matched values are never
printed or included in the saved report. An output report with findings fails.
Gitleaks' built-in rules/allowlists apply; absence of findings is not a proof that
all possible secrets are absent. Deleted/unadvertised remote refs, Git LFS content,
and nested archives deeper than three levels are outside this automated scope.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile

from check_caddy_runtime import Archive, layer_names, require


def run(*arguments, timeout=180):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GITLEAKS_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout, env=env)


def scan(gitleaks, mode, target, directory, log_opts=None):
    require(run(gitleaks, "version").stdout.strip() == "8.30.1", "Expected Gitleaks 8.30.1")
    config = directory / "rules.toml"
    # The inherited official Python image records this public signing-key
    # fingerprint as GPG_KEY. It is not a private key or authentication token.
    # Match only this exact public value, never whole paths or generic keys.
    config.write_text(
        '[extend]\nuseDefault = true\n\n'
        '[allowlist]\ndescription = "Official Python public signing fingerprint"\n'
        'regexTarget = "secret"\n'
        'regexes = ["^7169605F62C751356D054A26A821E680E5FA6305$"]\n'
    )
    ignore = directory / "empty-ignore"
    ignore.mkdir(exist_ok=True)
    report = directory / "raw-redacted.json"
    args = [gitleaks, mode, str(target), "--no-banner", "--no-color", "--redact=100",
            "--config", str(config), "--gitleaks-ignore-path", str(ignore),
            "--ignore-gitleaks-allow", "--max-archive-depth=3", "--max-decode-depth=5",
            "--max-target-megabytes=0", "--timeout=150", "--report-format=json",
            "--report-path", str(report)]
    if log_opts:
        args.append("--log-opts=" + log_opts)
    result = run(*args)
    # Scanner diagnostics are captured in memory; never echo possibly sensitive
    # messages, full match lines, passwords or environment values on failure.
    require(result.returncode in (0, 1) and report.is_file(), "Secret scanner did not complete")
    values = json.loads(report.read_text())
    require(isinstance(values, list), "Invalid secret scanner report")
    require((result.returncode == 1) == bool(values), "Secret scanner exit/report disagree")
    allowed = ("RuleID", "File", "StartLine", "EndLine", "Commit")
    return [{key: value[key] for key in allowed if key in value} for value in values]


def materialize_layers(archive, image, directory, platform="linux/amd64"):
    """Keep all regular payloads separately; never apply deletion/overwrite rules."""
    layers_report = []
    total = 0
    with Archive(archive) as source:
        config, layers = layer_names(source, image, platform)
        raw_config = source.config(image, platform)
        (directory / "image-config.json").write_bytes(raw_config)
        expected = config["rootfs"]["diff_ids"]
        require(len(layers) == len(expected), "Image layer/config count mismatch")
        for number, layer_name in enumerate(layers):
            member = source.members.get(str(PurePosixPath(layer_name)))
            require(member is not None and member.isfile(), "Missing image layer")
            # Verify the uncompressed tar hash recorded by the image config.
            with source.tar.extractfile(member) as stream:
                magic = stream.read(2)
            require(magic != b"\x28\xb5", "Zstd layers require an uncompressed Docker save archive")
            import gzip
            with source.tar.extractfile(member) as original:
                stream = gzip.GzipFile(fileobj=original) if magic == b"\x1f\x8b" else original
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            require("sha256:" + digest.hexdigest() == expected[number], "Image layer digest mismatch")
            layer_root = directory / f"layer-{number:04d}"
            layer_root.mkdir()
            files = 0
            metadata = []
            with source.tar.extractfile(member) as stream:
                with tarfile.open(fileobj=stream, mode="r|*") as layer:
                    for sequence, item in enumerate(layer):
                        path = PurePosixPath(item.name)
                        require(not path.is_absolute() and ".." not in path.parts,
                                "Unsafe image path")
                        require(sequence < 250000, "Too many image members")
                        # Store by sequence so duplicate names are scanned too.
                        metadata.append({"entry": sequence, "path": str(path),
                                         "type": item.type.decode("ascii", errors="replace"),
                                         "link": item.linkname})
                        if not item.isfile():
                            continue
                        total += item.size
                        require(total <= 4 * 1024**3, "Image secret scan exceeds the audited byte limit")
                        # Preserve extensions to enable nested-archive detection.
                        name = f"{sequence:06d}-" + (path.name or "payload")
                        with layer.extractfile(item) as content, (layer_root / name).open("wb") as out:
                            shutil.copyfileobj(content, out)
                        files += 1
            (layer_root / "member-paths.json").write_text(json.dumps(metadata))
            layers_report.append({"diff_id": expected[number], "regular_files": files,
                                  "entries": len(metadata)})
    return {"image": image, "config": "sha256:" + hashlib.sha256(raw_config).hexdigest(),
            "platform": platform, "layers": layers_report, "regular_payload_bytes": total}


def audit_git(gitleaks, repository, directory):
    require(run("git", "-C", repository, "rev-parse", "--is-shallow-repository").stdout.strip() == "false",
            "Full history requires a non-shallow clone")
    refs = run("git", "-C", repository, "for-each-ref", "--format=%(refname)\t%(objectname)")
    require(refs.returncode == 0 and refs.stdout.strip(), "Git refs are missing")
    commits = run("git", "-C", repository, "rev-list", "--all", "--count")
    require(commits.returncode == 0, "Git history cannot be enumerated")
    findings = scan(gitleaks, "git", repository, directory,
                    "--all --full-history --root --diff-merges=first-parent")
    after = run("git", "-C", repository, "for-each-ref", "--format=%(refname)\t%(objectname)")
    require(after.stdout == refs.stdout, "Git refs changed during the secret scan")
    return {"scope": "all commits reachable from the recorded fetched refs",
            "commits": int(commits.stdout.strip()), "refs": refs.stdout.splitlines(),
            "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitleaks", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repository")
    parser.add_argument("--archive")
    parser.add_argument("--image")
    parser.add_argument("--expected-config")
    parser.add_argument("--platform", default="linux/amd64")
    args = parser.parse_args()
    require(bool(args.repository) != bool(args.archive), "Select Git history or an image archive")
    with tempfile.TemporaryDirectory(prefix="score-it-secret-audit-") as temporary:
        directory = Path(temporary)
        if args.repository:
            result = audit_git(args.gitleaks, args.repository, directory)
        else:
            require(args.image and args.expected_config, "Image identity must be specified")
            contents = directory / "contents"
            contents.mkdir()
            result = materialize_layers(args.archive, args.image, contents, args.platform)
            require(result["config"] == args.expected_config, "Image config differs from release manifest")
            result["findings"] = scan(args.gitleaks, "dir", contents, directory)
    result.update(scanner="Gitleaks 8.30.1", checked_at=datetime.now(timezone.utc).isoformat(),
                  rule_profile="built-in rules; exact public Python GPG fingerprint exception; no repo config, ignore file, baseline or inline allows",
                  reviewed_public_values=["7169605F62C751356D054A26A821E680E5FA6305"],
                  max_archive_depth=3, max_decode_depth=5)
    output = Path(args.output)
    with output.open("w") as stream:
        os.chmod(output, 0o600)
        json.dump(result, stream, indent=2)
        stream.write("\n")
    require(not result["findings"], "SECRET_AUDIT_REVIEW_REQUIRED: inspect the redacted report")
    print("GIT_HISTORY_SECRETS_OK" if args.repository else "IMAGE_ALL_LAYERS_SECRETS_OK=" + args.image)


if __name__ == "__main__":
    main()
