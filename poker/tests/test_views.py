from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from openpyxl import load_workbook

from poker.models import (
    Participant,
    Project,
    Sprint,
    SprintTask,
    Task,
    Vote,
    VotingRound,
    VotingSession,
)


class OrganizerViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="secret")
        self.other_user = get_user_model().objects.create_user(
            "other", password="secret"
        )
        self.project = Project.objects.create(owner=self.user, name="ABS Core")

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("poker:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_bulk_import_creates_and_updates_tasks(self):
        self.client.force_login(self.user)
        url = reverse("poker:task_import", args=[self.project.pk])

        response = self.client.post(
            url,
            {"tasks_text": "ABS-1 | Первая задача\nABS-2 Вторая задача"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.project.tasks.count(), 2)

        self.client.post(url, {"tasks_text": "ABS-1 | Обновлённое название"})
        self.assertEqual(self.project.tasks.count(), 2)
        self.assertEqual(
            self.project.tasks.get(number="ABS-1").title, "Обновлённое название"
        )

    def test_other_organizer_cannot_open_project(self):
        self.client.force_login(self.other_user)
        response = self.client.get(self.project.get_absolute_url())
        self.assertEqual(response.status_code, 404)


class VotingFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="secret")
        self.project = Project.objects.create(owner=self.user, name="ABS Core")
        self.task = Task.objects.create(
            project=self.project, number="ABS-101", title="Рассчитать комиссию"
        )
        self.voting_session = VotingSession.objects.create(
            project=self.project, name="Оценка спринта"
        )
        self.organizer = Client()
        self.organizer.force_login(self.user)

    def join_participant(self, name):
        client = Client()
        response = client.post(
            reverse("poker:room_join", args=[self.voting_session.public_token]),
            {"name": name},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_complete_voting_flow_saves_exact_average(self):
        first = self.join_participant("Анна")
        second = self.join_participant("Борис")

        response = self.organizer.post(
            reverse(
                "poker:session_start_task",
                args=[self.voting_session.pk, self.task.pk],
            )
        )
        self.assertEqual(response.status_code, 302)

        vote_url = reverse(
            "poker:room_vote", args=[self.voting_session.public_token]
        )
        self.assertEqual(first.post(vote_url, {"value": 2}).status_code, 200)
        self.assertEqual(second.post(vote_url, {"value": 8}).status_code, 200)

        self.organizer.post(
            reverse("poker:session_reveal", args=[self.voting_session.pk])
        )
        state = first.get(
            reverse("poker:room_state", args=[self.voting_session.public_token])
        ).json()
        self.assertEqual(state["round"]["status"], VotingRound.Status.REVEALED)
        self.assertEqual(state["round"]["average"], "5")
        self.assertEqual(len(state["round"]["votes"]), 2)

        self.organizer.post(
            reverse("poker:session_accept", args=[self.voting_session.pk])
        )
        self.task.refresh_from_db()
        self.voting_session.refresh_from_db()

        self.assertEqual(self.task.estimate_sum, 10)
        self.assertEqual(self.task.estimate_count, 2)
        self.assertEqual(self.task.estimate_display, "5")
        self.assertIsNone(self.voting_session.current_task)

    def test_vote_rejects_value_outside_scale(self):
        participant = self.join_participant("Анна")
        self.organizer.post(
            reverse(
                "poker:session_start_task",
                args=[self.voting_session.pk, self.task.pk],
            )
        )
        response = participant.post(
            reverse("poker:room_vote", args=[self.voting_session.public_token]),
            {"value": 5},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Vote.objects.count(), 0)

    def test_participant_name_must_be_unique_in_room(self):
        self.join_participant("Анна")
        second = Client()
        response = second.post(
            reverse("poker:room_join", args=[self.voting_session.public_token]),
            {"name": "анна"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Это имя уже используется", status_code=400)
        self.assertEqual(Participant.objects.count(), 1)


class SprintTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="secret")
        self.project = Project.objects.create(owner=self.user, name="ABS Core")
        self.task = Task.objects.create(
            project=self.project,
            number="ABS-10",
            title="Выгрузка для ЦБ",
            status=Task.Status.ESTIMATED,
            estimate_sum=14,
            estimate_count=3,
        )
        self.sprint = Sprint.objects.create(
            project=self.project, name="Спринт 24", capacity=20
        )
        SprintTask.objects.create(sprint=self.sprint, task=self.task, position=1)
        self.client.force_login(self.user)

    def test_sprint_total_uses_exact_task_average(self):
        self.assertEqual(self.sprint.total_estimate, self.task.estimate)
        self.assertEqual(self.sprint.total_estimate_display, "4.67")

    def test_excel_export_contains_average_formula(self):
        response = self.client.get(
            reverse("poker:sprint_export", args=[self.sprint.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        workbook = load_workbook(BytesIO(response.content), data_only=False)
        sheet = workbook["Задачи спринта"]
        self.assertEqual(sheet["B2"].value, "ABS-10")
        self.assertEqual(sheet["D2"].value, "=E2/F2")
        self.assertEqual(sheet["E2"].value, 14)
        self.assertEqual(sheet["F2"].value, 3)

