import http.server
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "build_tls.sh"


def invoke(mode, command, env=None):
    return subprocess.run(
        ["sh", str(SCRIPT), str(mode), *command],
        env=env, text=True, capture_output=True, timeout=15,
    )


class BuildTLSProfileTests(unittest.TestCase):
    def test_default_preserves_existing_environment_and_exit_status(self):
        env = dict(os.environ, OPENSSL_CONF="/existing/config", GODEBUG="gctrace=1")
        result = invoke(0, [sys.executable, "-c", (
            "import json, os, sys; "
            "print(json.dumps([os.environ['OPENSSL_CONF'], os.environ['GODEBUG']])); "
            "sys.exit(7)"
        )], env)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["/existing/config", "gctrace=1"])

    def test_compatibility_profile_is_removed_after_success_and_failure(self):
        for status in (0, 7):
            with self.subTest(status=status):
                env = dict(os.environ, GODEBUG="gctrace=1")
                result = invoke(1, [sys.executable, "-c", (
                    "import json, os, pathlib, sys; "
                    "path = pathlib.Path(os.environ['OPENSSL_CONF']); "
                    "assert path.is_file(); "
                    "print(json.dumps({'path': str(path), 'debug': os.environ['GODEBUG']})); "
                    f"sys.exit({status})"
                )], env)
                self.assertEqual(result.returncode, status, result.stderr)
                data = json.loads(result.stdout)
                self.assertFalse(Path(data["path"]).exists())
                self.assertIn("gctrace=1", data["debug"])
                self.assertIn("tlsmlkem=0", data["debug"])
                self.assertIn("tlssecpmlkem=0", data["debug"])

    def test_invalid_mode_does_not_run_the_command(self):
        result = invoke("invalid", [sys.executable, "-c", "print('COMMAND_RAN')"])
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("COMMAND_RAN", result.stdout)

    def test_missing_command_is_rejected(self):
        result = subprocess.run(["sh", str(SCRIPT), "1"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)

    @unittest.skipUnless(shutil.which("curl") and shutil.which("openssl"), "curl/openssl required")
    def test_https_still_requires_a_trusted_certificate(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"TLS_TEST_OK")

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / "cert.pem"
            key = Path(directory) / "key.pem"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-sha256", "-days", "1", "-subj", "/CN=localhost",
                "-addext", "subjectAltName=IP:127.0.0.1",
                "-keyout", str(key), "-out", str(cert),
            ], check=True, capture_output=True, timeout=15)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                command = ["curl", "--noproxy", "*", "-fsS", "--max-time", "5"]
                url = f"https://127.0.0.1:{server.server_port}/"
                for mode in (0, 1):
                    with self.subTest(mode=mode):
                        rejected = invoke(mode, [*command, url])
                        self.assertEqual(rejected.returncode, 60, rejected.stderr)
                        trusted = invoke(mode, [*command, "--cacert", str(cert), url])
                        self.assertEqual(trusted.returncode, 0, trusted.stderr)
                        self.assertEqual(trusted.stdout, "TLS_TEST_OK")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
