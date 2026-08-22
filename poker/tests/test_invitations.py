from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from poker.models import OrganizerInvitation, Project


class OrganizerInvitationTests(TestCase):
    password = "M8!gration-River-2026"

    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            "root", password="Admin-River-2026!"
        )

    def create_invitation(self, **kwargs):
        return OrganizerInvitation.objects.create(
            created_by=self.superuser,
            recipient_label="Новый организатор",
            **kwargs,
        )

    def test_superuser_creates_invitation_in_admin(self):
        client = Client()
        client.force_login(self.superuser)

        response = client.post(
            reverse("admin:poker_organizerinvitation_add"),
            {"recipient_label": "Команда переводов", "_save": "Сохранить"},
        )

        self.assertEqual(response.status_code, 302)
        invitation = OrganizerInvitation.objects.get()
        self.assertEqual(invitation.created_by, self.superuser)
        self.assertEqual(invitation.recipient_label, "Команда переводов")
        self.assertTrue(invitation.is_available)
        self.assertGreater(invitation.expires_at, timezone.now() + timedelta(days=6))

        response = client.get(
            reverse("admin:poker_organizerinvitation_changelist")
        )
        self.assertContains(response, "data-invitation-path")
        self.assertContains(response, invitation.get_absolute_url())

        response = client.get(reverse("poker:dashboard"))
        self.assertContains(response, "Админка")

    def test_invitation_creates_non_staff_organizer_and_logs_them_in(self):
        private_project = Project.objects.create(
            owner=self.superuser, name="Проект суперпользователя"
        )
        invitation = self.create_invitation()
        client = Client()

        response = client.post(
            invitation.get_absolute_url(),
            {
                "username": "organizer",
                "password1": self.password,
                "password2": self.password,
            },
        )

        self.assertRedirects(response, reverse("poker:dashboard"))
        organizer = get_user_model().objects.get(username="organizer")
        self.assertTrue(organizer.is_active)
        self.assertFalse(organizer.is_staff)
        self.assertFalse(organizer.is_superuser)

        invitation.refresh_from_db()
        self.assertEqual(invitation.used_by, organizer)
        self.assertIsNotNone(invitation.used_at)
        self.assertFalse(invitation.is_available)
        self.assertEqual(
            int(client.session["_auth_user_id"]),
            organizer.pk,
        )

        response = client.post(
            reverse("poker:project_create"), {"name": "Свой проект"}
        )
        created_project = Project.objects.get(name="Свой проект")
        self.assertRedirects(response, created_project.get_absolute_url())
        self.assertEqual(created_project.owner, organizer)

        response = client.get(reverse("poker:dashboard"))
        self.assertContains(response, "Свой проект")
        self.assertNotContains(response, private_project.name)
        self.assertNotContains(response, "Админка")

        response = client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)

    def test_invalid_registration_does_not_consume_invitation(self):
        invitation = self.create_invitation()

        response = self.client.post(
            invitation.get_absolute_url(),
            {
                "username": "organizer",
                "password1": self.password,
                "password2": "another-password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Введенные пароли не совпадают")
        invitation.refresh_from_db()
        self.assertTrue(invitation.is_available)
        self.assertIsNone(invitation.used_by)

    def test_used_invitation_cannot_create_second_account(self):
        invitation = self.create_invitation()
        first_client = Client()
        first_client.post(
            invitation.get_absolute_url(),
            {
                "username": "first-organizer",
                "password1": self.password,
                "password2": self.password,
            },
        )

        response = Client().get(invitation.get_absolute_url())

        self.assertEqual(response.status_code, 410)
        self.assertContains(
            response,
            "Приглашение уже использовано",
            status_code=410,
        )
        self.assertEqual(get_user_model().objects.filter(is_superuser=False).count(), 1)

    def test_expired_invitation_is_rejected(self):
        invitation = self.create_invitation(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        response = self.client.get(invitation.get_absolute_url())

        self.assertEqual(response.status_code, 410)
        self.assertContains(response, "Срок приглашения истёк", status_code=410)
