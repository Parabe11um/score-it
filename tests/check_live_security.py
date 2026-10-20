"""Exercise real Gunicorn HTTP and logs on an isolated score-it-check-* project.

Uses only the host standard library. Two temporary staff accounts are deleted
after the check. Never print or save their credentials or unfiltered web logs.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


class CheckFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CheckFailure(message)


def docker(*arguments, source=None, timeout=30):
    options = {"input": source} if source is not None else {"stdin": subprocess.DEVNULL}
    result = subprocess.run(
        ["docker", *arguments], text=True, capture_output=True, timeout=timeout, **options,
    )
    require(result.returncode == 0, "Docker operation failed: " + result.stderr[-2000:])
    return result.stdout + (result.stderr if arguments[0] == "logs" else "")


def check_inside(args):
    import http.client
    from http.cookies import SimpleCookie
    from io import StringIO
    import secrets
    from urllib.parse import urlencode

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import connection

    require(os.geteuid() == 10001, "Web must use UID 10001")
    require(connection.vendor == "postgresql", "A PostgreSQL staging database is required")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        require(cursor.fetchone()[0] == args.database, "Unexpected staging database")
    call_command("check_database_security", stdout=StringIO())

    nonce = str(uuid.UUID(args.nonce))
    suffix = uuid.UUID(nonce).hex
    names = ["security_a_" + suffix, "security_b_" + suffix]
    passwords = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
    user_model = get_user_model()
    require(not user_model.objects.filter(username__in=names).exists(), "Probe accounts already exist")

    def request(path, form=None, cookie=""):
        client = http.client.HTTPConnection("127.0.0.1", 8000, timeout=10)
        # Same private HTTP hop and HTTPS proxy header used behind Caddy.
        headers = {"Host": "localhost", "X-Forwarded-Proto": "https"}
        body = None
        if cookie:
            headers["Cookie"] = cookie
        if form is not None:
            body = urlencode(form)
            headers.update({"Content-Type": "application/x-www-form-urlencoded",
                            "Origin": "https://localhost", "Referer": "https://localhost" + path})
        try:
            client.request("POST" if form is not None else "GET", path, body, headers)
            response = client.getresponse()
            response.read()
            return response.status, response.getheaders()
        finally:
            client.close()

    def login(path, name, password, csrf=True):
        status, headers = request(path)
        require(status == 200, "Login form returned HTTP " + str(status))
        cookies = SimpleCookie()
        for key, value in headers:
            if key.lower() == "set-cookie":
                cookies.load(value)
        require("csrftoken" in cookies, "Login form did not issue a CSRF cookie")
        form = {"username": name, "password": password}
        if csrf:
            form["csrfmiddlewaretoken"] = cookies["csrftoken"].value
        cookie = "; ".join(key + "=" + value.value for key, value in cookies.items())
        status, headers = request(path, form, cookie)
        return status, {key.lower(): value for key, value in headers}

    checks = {}
    try:
        for name, password in zip(names, passwords):
            user_model.objects.create_user(name, password=password, is_staff=True)
        require(login("/login/", names[0], passwords[0])[0] == 302, "Baseline login failed")
        require(login("/login/", names[0], passwords[0], csrf=False)[0] == 403,
                "Login accepted a request without a CSRF token")
        checks["csrf_rejected"] = 403
        mixed = [login("/login/" if i % 2 == 0 else "/admin/login/", names[0], "wrong-password")[0]
                 for i in range(5)]
        require(mixed == [200, 200, 200, 200, 429], "Unexpected mixed login failures: " + str(mixed))
        checks["shared_failure_counter"] = mixed
        for path in ("/login/", "/admin/login/"):
            status, headers = login(path, names[0], passwords[0])
            require(status == 429 and headers.get("retry-after") == "900",
                    "Correct credentials bypassed lockout or Retry-After differs")
            require("no-store" in headers.get("cache-control", ""), "Lockout response is cacheable")
        checks["correct_password_blocked_on_both_forms"] = True
        checks["retry_after_seconds"] = 900
        for path in ("/login/", "/admin/login/"):
            require(login(path, names[1], passwords[1])[0] == 302, "Another account behind the same IP was blocked")
        checks["other_account_same_ip_can_login"] = True
        admin = [login("/admin/login/", names[1], "wrong-password")[0] for _ in range(5)]
        require(admin == [200, 200, 200, 200, 429], "Unexpected admin login failures: " + str(admin))
        require(login("/login/", names[1], passwords[1])[0] == 429, "Admin lockout did not protect ordinary login")
        checks["admin_failure_counter"] = admin
        for path in ("/room/" + nonce + "/", "/organizer/invite/" + nonce + "/",
                     "/room/" + nonce.replace("-", "%2D") + "/resume/" + nonce + "/"):
            status, headers = request(path)
            headers = {key.lower(): value for key, value in headers}
            require(status == 404, "Synthetic token unexpectedly exists")
            require("no-store" in headers.get("cache-control", ""), "Token response is cacheable")
            require(headers.get("referrer-policy") == "no-referrer", "Token response lacks no-referrer")
        checks["token_privacy_headers"] = True
        require(request("/security_probe_" + suffix + "/")[0] == 404, "Log control request failed")
    finally:
        user_model.objects.filter(username__in=names).delete()
    require(not user_model.objects.filter(username__in=names).exists(), "Probe account cleanup failed")
    checks["probe_accounts_removed"] = True
    # This private response is captured by the host, never printed or saved there.
    print(json.dumps({"checks": checks, "_probes": names + passwords}))


def check_host(args):
    require(re.fullmatch(r"score-it-check-[a-z0-9][a-z0-9_-]*", args.project or ""),
            "Use a dedicated score-it-check-* Compose project")
    require(re.fullmatch(r"[0-9a-f]{40}", args.release or ""), "A full release SHA is required")
    require(re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", args.database or ""), "A staging database name is required")
    identifiers = docker("ps", "--filter", "label=com.docker.compose.project=" + args.project,
                         "--filter", "label=com.docker.compose.service=web", "--format", "{{.ID}}").split()
    require(len(identifiers) == 1, "Expected exactly one running staging web container")
    container = json.loads(docker("inspect", identifiers[0]))[0]
    require(container["Config"]["Image"] == "score-it:" + args.release, "Unexpected staging image")
    require(container["Config"]["Labels"].get("org.opencontainers.image.revision") == args.release,
            "Staging image revision does not match the release")
    require(container["State"].get("Health", {}).get("Status") == "healthy", "Staging web is not healthy")
    require(not container["HostConfig"].get("PortBindings"), "Use an unpublished isolated staging web container")
    networks = container["NetworkSettings"]["Networks"]
    require(bool(networks), "Staging has no private network")
    for network in networks:
        require(json.loads(docker("network", "inspect", network))[0]["Internal"],
                "Staging must use only internal Docker networks")

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nonce = str(uuid.uuid4())
    raw = docker("exec", "-i", container["Id"], "python", "-", "--inside",
                 "--database", args.database, "--nonce", nonce,
                 source=Path(__file__).read_text(), timeout=120)
    result = json.loads(raw)
    probes = result.pop("_probes") + [nonce, nonce.replace("-", "%2D")]
    control = "/security_probe_" + uuid.UUID(nonce).hex + "/"
    logs = ""
    for _ in range(15):
        logs = docker("logs", "--since", started, container["Id"])
        if control in logs:
            break
        time.sleep(0.2)
    require(control in logs, "Control request is absent from collected web logs")
    require(not any(value.lower() in logs.lower() for value in probes),
            "Web logs contain an unmasked synthetic token or test credentials")
    require("/room/[redacted]/" in logs and "/organizer/invite/[redacted]/" in logs,
            "Expected redacted token requests are absent from web logs")
    require("LOGIN_SUCCESS account=" in logs and "LOGIN_FAILURE account=" in logs,
            "Login audit events are absent from web logs")
    result["checks"].update(web_log_privacy=True, login_audit_present=True)
    result.update(release=args.release, project=args.project, database=args.database,
                  image=container["Config"]["Image"], checked_at=started,
                  scope="Django/Gunicorn HTTP and web logs; Caddy/TLS are separate checks")
    if args.output:
        output = Path(args.output)
        with output.open("w") as handle:
            os.chmod(output, 0o600)
            json.dump(result, handle, indent=2)
            handle.write("\n")
    for marker in ("BRUTE_FORCE_OK", "CSRF_OK", "TOKEN_HEADERS_OK", "WEB_LOG_PRIVACY_OK", "LIVE_SECURITY_OK"):
        print(marker)
    if args.output:
        print("REPORT=" + str(args.output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project")
    parser.add_argument("--release")
    parser.add_argument("--database", required=True)
    parser.add_argument("--output")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--nonce", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.inside:
            check_inside(args)
        else:
            check_host(args)
    except (CheckFailure, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, "LIVE_SECURITY_FAILED: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
