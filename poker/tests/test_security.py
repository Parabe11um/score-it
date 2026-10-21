from datetime import timedelta
from io import StringIO
import logging
import uuid

from axes.models import AccessAttempt
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from config.security import RedactingFormatter


class LogPrivacyTests(SimpleTestCase):
    def test_tokens_in_message_arguments_referer_and_exception_are_redacted(self):
        token = str(uuid.uuid4())
        output = StringIO()
        logger = logging.Logger("privacy-test")
        handler = logging.StreamHandler(output)
        handler.setFormatter(RedactingFormatter("%(message)s"))
        logger.addHandler(handler)
        try:
            raise ValueError("https://score-it/room/" + token + "/resume/" + token + "/")
        except ValueError:
            logger.exception("GET %s Referer=%s", "/organizer/invite/" + token + "/", token.replace("-", "%2D"))
        result = output.getvalue()
        self.assertNotIn(token, result)
        self.assertNotIn(token.replace("-", "%2D"), result)
        self.assertIn("/room/[redacted]/resume/[redacted]/", result)
        self.assertIn("ValueError", result)

    def test_gunicorn_formats_access_and_error_logs_safely(self):
        try:
            from gunicorn.config import Config
            from deploy.gunicorn_logger import SafeLogger
        except ImportError:
            self.skipTest("Gunicorn is only installed for production")
        config = Config()
        config.set("accesslog", "-")
        logger = SafeLogger(config)
        token = str(uuid.uuid4())
        for log in (logger.access_log, logger.error_log):
            record = log.makeRecord(log.name, logging.WARNING, __file__, 1,
                                    "request /room/%s/resume/%s/", (token, token), None)
            self.assertTrue(log.handlers)
            for handler in log.handlers:
                self.assertNotIn(token, handler.format(record))


class LoginProtectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("protected", password="correct-long-password", is_staff=True)
        cls.other = get_user_model().objects.create_user("colleague", password="other-long-password")

    def fail_login(self, path="/login/", **headers):
        return self.client.post(path, {"username": "protected", "password": "wrong-password"}, **headers)

    def test_regular_and_admin_login_share_database_counter_and_cannot_bypass_lockout(self):
        for i in range(5):
            response = self.fail_login("/admin/login/" if i % 2 else "/login/",
                                       REMOTE_ADDR=f"192.0.2.{i+1}", HTTP_USER_AGENT=f"agent-{i}", HTTP_X_FORWARDED_FOR=f"203.0.113.{i+1}")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "900")
        client = Client(REMOTE_ADDR="198.51.100.7")
        response = client.post("/admin/login/", {"username": "protected", "password": "correct-long-password"})
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", client.session)

    def test_corporate_nat_does_not_lock_out_another_account(self):
        for _ in range(5):
            self.fail_login()
        response = Client().post("/login/", {"username": "colleague", "password": "other-long-password"})
        self.assertEqual(response.status_code, 302)

    def test_lockout_expires_and_success_clears_failure_counter(self):
        for _ in range(5):
            self.fail_login()
        AccessAttempt.objects.update(attempt_time=timezone.now() - timedelta(minutes=16))
        response = self.client.post("/login/", {"username": "protected", "password": "correct-long-password"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AccessAttempt.objects.filter(username="protected").exists())

    def test_bad_csrf_is_rejected_and_does_not_lock_account(self):
        response = Client(enforce_csrf_checks=True).post("/login/", {"username": "protected", "password": "wrong"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AccessAttempt.objects.exists())

    def test_authentication_events_do_not_log_credentials(self):
        with self.assertLogs("score_it.auth", level="INFO") as logs:
            self.fail_login()
            self.client.post("/login/", {"username": "protected", "password": "correct-long-password"})
        text = "\n".join(logs.output)
        self.assertIn("LOGIN_FAILURE", text)
        self.assertIn("LOGIN_SUCCESS", text)
        self.assertNotIn("protected", text)
        self.assertNotIn("password", text)


class TokenHeadersTests(TestCase):
    def test_invalid_links_are_also_private_and_not_cached(self):
        for path in (f"/room/{uuid.uuid4()}/", f"/organizer/invite/{uuid.uuid4()}/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertEqual(response["Referrer-Policy"], "strict-origin")
