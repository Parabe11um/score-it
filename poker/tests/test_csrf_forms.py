"""Native HTTPS forms must work without weakening CSRF or token privacy."""

from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from poker.models import Project, Task, VotingRound, VotingSession, VotingSessionTask


class CsrfFieldParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "csrfmiddlewaretoken":
            self.tokens.append(attrs["value"])


@override_settings(
    DEBUG=False,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
)
class HttpsFormCsrfTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("form-owner", password="long-test-password")
        cls.other = get_user_model().objects.create_user("form-other", password="other-test-password")
        cls.project = Project.objects.create(owner=cls.user, name="CSRF regression")

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.task = Task.objects.create(project=self.project, number="CSRF-1", title="Unfinished task")
        self.room = VotingSession.objects.create(
            project=self.project, name="Close session", status=VotingSession.Status.ACTIVE,
            current_task=self.task,
        )
        self.round = VotingRound.objects.create(session=self.room, task=self.task, number=1)
        self.queue_item = VotingSessionTask.objects.create(
            session=self.room, task=self.task, position=1,
            status=VotingSessionTask.Status.ACTIVE, current_round=self.round,
        )
        self.finish_url = reverse("poker:session_finish", args=[self.room.pk])

    def form_token(self, client, url):
        response = client.get(url, secure=True)
        self.assertEqual(response.status_code, 200)
        # no-referrer makes native POST forms send Origin: null (Fetch standard).
        # strict-origin preserves the real origin and omits paths and queries.
        self.assertEqual(response["Referrer-Policy"], "strict-origin")
        parser = CsrfFieldParser()
        parser.feed(response.content.decode())
        self.assertTrue(parser.tokens, "Rendered form must contain a CSRF field")
        return parser.tokens[0]

    def assert_room_active(self):
        self.room.refresh_from_db()
        self.round.refresh_from_db()
        self.queue_item.refresh_from_db()
        self.assertEqual(self.room.status, VotingSession.Status.ACTIVE)
        self.assertIsNone(self.room.finished_at)
        self.assertEqual(self.round.status, VotingRound.Status.VOTING)
        self.assertEqual(self.queue_item.status, VotingSessionTask.Status.ACTIVE)

    def test_owner_can_finish_with_rendered_csrf_token_over_https(self):
        token = self.form_token(self.client, self.room.get_absolute_url())
        response = self.client.post(
            self.finish_url, {"csrfmiddlewaretoken": token},
            secure=True, HTTP_ORIGIN="https://testserver", HTTP_REFERER="https://testserver/",
        )
        self.assertRedirects(response, self.room.get_absolute_url(), fetch_redirect_response=False)
        self.room.refresh_from_db()
        self.round.refresh_from_db()
        self.queue_item.refresh_from_db()
        self.assertEqual(self.room.status, VotingSession.Status.FINISHED)
        self.assertIsNotNone(self.room.finished_at)
        self.assertIsNone(self.room.current_task_id)
        self.assertEqual(self.round.status, VotingRound.Status.CANCELLED)
        self.assertEqual(self.queue_item.status, VotingSessionTask.Status.SKIPPED)

    def test_https_referer_without_origin_is_still_accepted(self):
        token = self.form_token(self.client, self.room.get_absolute_url())
        response = self.client.post(
            self.finish_url, {"csrfmiddlewaretoken": token},
            secure=True, HTTP_REFERER="https://testserver/",
        )
        self.assertEqual(response.status_code, 302)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, VotingSession.Status.FINISHED)

    def test_null_or_foreign_origin_is_rejected_even_with_valid_token(self):
        token = self.form_token(self.client, self.room.get_absolute_url())
        for origin in ("null", "https://attacker.invalid"):
            with self.subTest(origin=origin):
                response = self.client.post(
                    self.finish_url, {"csrfmiddlewaretoken": token},
                    secure=True, HTTP_ORIGIN=origin,
                )
                self.assertEqual(response.status_code, 403)
                self.assert_room_active()

    def test_missing_or_wrong_csrf_token_is_rejected(self):
        self.form_token(self.client, self.room.get_absolute_url())
        for form in ({}, {"csrfmiddlewaretoken": "A" * 64}):
            response = self.client.post(self.finish_url, form, secure=True, HTTP_ORIGIN="https://testserver")
            self.assertEqual(response.status_code, 403)
            self.assert_room_active()

    def test_missing_cookie_is_rejected(self):
        token = self.form_token(self.client, self.room.get_absolute_url())
        del self.client.cookies["csrftoken"]
        response = self.client.post(
            self.finish_url, {"csrfmiddlewaretoken": token}, secure=True, HTTP_ORIGIN="https://testserver",
        )
        self.assertEqual(response.status_code, 403)
        self.assert_room_active()

    def test_valid_token_does_not_allow_another_organizer_to_close_room(self):
        self.client.force_login(self.other)
        token = self.form_token(self.client, reverse("poker:dashboard"))
        response = self.client.post(
            self.finish_url, {"csrfmiddlewaretoken": token}, secure=True, HTTP_ORIGIN="https://testserver",
        )
        self.assertEqual(response.status_code, 404)
        self.assert_room_active()

    def test_participant_can_join_from_token_page(self):
        participant = Client(enforce_csrf_checks=True)
        token = self.form_token(participant, self.room.get_public_url())
        response = participant.post(
            reverse("poker:room_join", args=[self.room.public_token]),
            {"name": "Test participant", "csrfmiddlewaretoken": token},
            secure=True, HTTP_ORIGIN="https://testserver", HTTP_REFERER="https://testserver/",
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.room.participants.filter(name="Test participant").exists())

    def test_login_from_native_https_form(self):
        anonymous = Client(enforce_csrf_checks=True)
        token = self.form_token(anonymous, reverse("login"))
        response = anonymous.post(
            reverse("login"),
            {"username": "form-owner", "password": "long-test-password", "csrfmiddlewaretoken": token},
            secure=True, HTTP_ORIGIN="https://testserver", HTTP_REFERER="https://testserver/",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(anonymous.session["_auth_user_id"]), self.user.pk)
