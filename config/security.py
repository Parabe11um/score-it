"""Protect bearer links without changing URLs used by participants."""

import ipaddress
import logging
import re

from django.conf import settings
from django.http import HttpResponse
from django.utils.crypto import salted_hmac
from django.views.debug import ExceptionReporter


# Tokens are UUIDs; include percent-encoded hyphens seen in raw HTTP request logs.
_UUID = re.compile(r"[0-9a-f]{8}(?:-|%2d)[0-9a-f]{4}(?:-|%2d)[0-9a-f]{4}(?:-|%2d)[0-9a-f]{4}(?:-|%2d)[0-9a-f]{12}", re.I)
_TOKEN_PATH = re.compile(r"(/(?:room|organizer/invite|resume)/)[^/\s?\#\"'<>]+", re.I)


def redact_tokens(value):
    value = _UUID.sub("[redacted]", str(value))
    return _TOKEN_PATH.sub(r"\1[redacted]", value)


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        # Format first: also covers %-arguments, referers and exception tracebacks.
        return redact_tokens(super().format(record))


class RedactingExceptionReporter(ExceptionReporter):
    def get_traceback_text(self):
        return redact_tokens(super().get_traceback_text())

    def get_traceback_html(self):
        return redact_tokens(super().get_traceback_html())


class TokenLinkPrivacyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(("/room/", "/organizer/invite/")):
            response["Cache-Control"] = "private, no-store"
            response["Referrer-Policy"] = settings.SECURE_REFERRER_POLICY
        return response


def direct_client_ip(request):
    try:
        return str(ipaddress.ip_address(request.META.get("REMOTE_ADDR", "")))
    except ValueError:
        return None


def login_locked_out(request, *args, **kwargs):
    response = HttpResponse(
        "Слишком много попыток входа. Повторите через 15 минут.",
        status=429, content_type="text/plain; charset=utf-8",
    )
    response["Retry-After"] = str(int(settings.AXES_COOLOFF_TIME.total_seconds()))
    response["Cache-Control"] = "no-store"
    return response


def record_login(sender, request, user=None, credentials=None, **kwargs):
    if request is None:
        return
    username = user.get_username() if user is not None else (credentials or {}).get("username", "")
    account = salted_hmac("score-it.auth-audit", str(username)).hexdigest()[:20]
    logging.getLogger("score_it.auth").info(
        "LOGIN_%s account=%s", "SUCCESS" if user is not None else "FAILURE", account,
    )
