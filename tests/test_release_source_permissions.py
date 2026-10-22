"""Exercise the real release export without building images or using Docker."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD_PROBE = r'''#!/usr/bin/env python3
import json, os, stat, sys
from pathlib import Path

if sys.argv[1] == 'version':
    print(json.dumps({'Version': '29.8.2'}))
    sys.exit(0)
assert sys.argv[1] == 'build', sys.argv[1:]
root = Path(sys.argv[-1])
paths = ('config', 'config/database.py', 'deploy', 'deploy/executable.sh')
report = {
    'modes': {name: stat.S_IMODE((root / name).stat().st_mode) for name in paths},
    'private_context': stat.S_IMODE(root.stat().st_mode),
    'source': (root / 'config/database.py').read_text(),
    'caller_umask': os.umask(0o077),
}
Path(os.environ['SOURCE_PROBE']).write_text(json.dumps(report))
# Stop before the next image build, image export, or scan.
sys.exit(42)
'''


class ReleaseSourcePermissionsTests(unittest.TestCase):
    def probe(self, caller_umask, git_tar_umask):
        with tempfile.TemporaryDirectory(prefix="score-it-source-test-") as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / "deploy").mkdir(parents=True)
            (repo / "config").mkdir()
            for name in ("make_release.sh", "images.env"):
                shutil.copyfile(ROOT / "deploy" / name, repo / "deploy" / name)
            (repo / "config/__init__.py").write_text("")
            (repo / "config/database.py").write_text("SOURCE_FROM_COMMIT = True\n")
            executable = repo / "deploy/executable.sh"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
            for args in (
                ("init", "--quiet"),
                ("config", "tar.umask", git_tar_umask),
                ("add", "."),
                ("-c", "user.name=Source test", "-c", "user.email=source-test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Source fixture"),
            ):
                subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                               capture_output=True, timeout=10)
            tools = root / "tools"
            tools.mkdir()
            docker = tools / "docker"
            docker.write_text(BUILD_PROBE)
            docker.chmod(0o755)
            output = root / "releases"
            probe = root / "source.json"
            env.update(PATH=str(tools) + os.pathsep + os.environ["PATH"],
                       RELEASE_PULL="1", SOURCE_PROBE=str(probe),
                       # Emulate non-root tar defaults even when tests run as root.
                       TAR_OPTIONS="--no-same-permissions")
            result = subprocess.run(
                ["bash", "-c", 'umask "$1"; exec bash deploy/make_release.sh "$2"',
                 "source-test", caller_umask, str(output)],
                cwd=repo, env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
            report = json.loads(probe.read_text())
            self.assertEqual(report["modes"], {
                "config": 0o755, "config/database.py": 0o644,
                "deploy": 0o755, "deploy/executable.sh": 0o755,
            })
            self.assertEqual(report["private_context"], 0o700)
            self.assertEqual(report["source"], "SOURCE_FROM_COMMIT = True\n")
            self.assertEqual(report["caller_umask"], int(caller_umask, 8))
            self.assertEqual(output.stat().st_mode & 0o777, 0o777 & ~int(caller_umask, 8))

    def test_source_is_readable_with_private_output_and_restrictive_git_settings(self):
        for caller_umask in ("022", "077"):
            for git_tar_umask in ("0002", "0077"):
                with self.subTest(caller_umask=caller_umask, git_tar_umask=git_tar_umask):
                    self.probe(caller_umask, git_tar_umask)


if __name__ == "__main__":
    unittest.main()
