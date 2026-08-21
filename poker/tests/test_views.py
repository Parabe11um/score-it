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
    VotingSessionTask,
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

    def test_project_page_offers_bulk_queue_and_minimum_votes(self):
        Task.objects.create(project=self.project, number="ABS-1", title="Первая")
        self.client.force_login(self.user)

        response = self.client.get(self.project.get_absolute_url())

        self.assertContains(response, "Создать комнату с очередью")
        self.assertContains(response, "Минимум голосов")
        self.assertContains(response, "checked")

    def test_session_creation_adds_selected_tasks_to_ordered_queue(self):
        first = Task.objects.create(
            project=self.project, number="ABS-1", title="Первая"
        )
        second = Task.objects.create(
            project=self.project, number="ABS-2", title="Вторая"
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("poker:session_create", args=[self.project.pk]),
            {
                "name": "Пакетная оценка",
                "minimum_participants": 3,
                "task_ids": [first.pk, second.pk],
            },
        )

        voting_session = VotingSession.objects.get(name="Пакетная оценка")
        self.assertRedirects(response, voting_session.get_absolute_url())
        self.assertEqual(voting_session.minimum_participants, 3)
        self.assertEqual(
            list(
                voting_session.queue_items.values_list("task_id", "position")
            ),
            [(first.pk, 1), (second.pk, 2)],
        )


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

    def test_reveal_waits_for_configured_minimum_votes(self):
        first = self.join_participant("Анна")
        self.organizer.post(
            reverse(
                "poker:session_start_task",
                args=[self.voting_session.pk, self.task.pk],
            )
        )
        first.post(
            reverse("poker:room_vote", args=[self.voting_session.public_token]),
            {"value": 8},
        )

        self.organizer.post(
            reverse("poker:session_reveal", args=[self.voting_session.pk])
        )

        self.voting_session.refresh_from_db()
        voting_round = self.voting_session.current_round
        self.assertEqual(voting_round.status, VotingRound.Status.VOTING)

    def test_accepting_estimate_starts_next_queued_task(self):
        second_task = Task.objects.create(
            project=self.project, number="ABS-102", title="Следующая задача"
        )
        VotingSessionTask.objects.bulk_create(
            [
                VotingSessionTask(
                    session=self.voting_session, task=self.task, position=1
                ),
                VotingSessionTask(
                    session=self.voting_session, task=second_task, position=2
                ),
            ]
        )
        first = self.join_participant("Анна")
        second = self.join_participant("Борис")
        self.organizer.post(
            reverse("poker:session_start", args=[self.voting_session.pk])
        )
        vote_url = reverse(
            "poker:room_vote", args=[self.voting_session.public_token]
        )
        first.post(vote_url, {"value": 4})
        second.post(vote_url, {"value": 8})
        self.organizer.post(
            reverse("poker:session_reveal", args=[self.voting_session.pk])
        )

        self.organizer.post(
            reverse("poker:session_accept", args=[self.voting_session.pk])
        )

        self.voting_session.refresh_from_db()
        self.assertEqual(self.voting_session.current_task, second_task)
        self.assertEqual(
            self.voting_session.queue_items.get(task=self.task).status,
            VotingSessionTask.Status.COMPLETED,
        )
        self.assertEqual(
            self.voting_session.queue_items.get(task=second_task).status,
            VotingSessionTask.Status.ACTIVE,
        )
        self.assertEqual(
            self.voting_session.current_round.status, VotingRound.Status.VOTING
        )

    def test_start_opens_every_queued_task_for_async_voting(self):
        second_task = Task.objects.create(
            project=self.project, number="ABS-102", title="Вторая задача"
        )
        VotingSessionTask.objects.bulk_create(
            [
                VotingSessionTask(
                    session=self.voting_session, task=self.task, position=1
                ),
                VotingSessionTask(
                    session=self.voting_session, task=second_task, position=2
                ),
            ]
        )

        self.organizer.post(
            reverse("poker:session_start", args=[self.voting_session.pk])
        )

        queue_items = list(
            self.voting_session.queue_items.select_related("current_round")
        )
        self.assertEqual(
            [item.status for item in queue_items],
            [VotingSessionTask.Status.ACTIVE, VotingSessionTask.Status.ACTIVE],
        )
        self.assertTrue(all(item.current_round_id for item in queue_items))
        self.assertEqual(
            {item.current_round.status for item in queue_items},
            {VotingRound.Status.VOTING},
        )

    def test_participants_navigate_and_vote_independently(self):
        second_task = Task.objects.create(
            project=self.project, number="ABS-102", title="Вторая задача"
        )
        VotingSessionTask.objects.bulk_create(
            [
                VotingSessionTask(
                    session=self.voting_session, task=self.task, position=1
                ),
                VotingSessionTask(
                    session=self.voting_session, task=second_task, position=2
                ),
            ]
        )
        anna = self.join_participant("Анна")
        boris = self.join_participant("Борис")
        room_response = anna.get(
            reverse("poker:room", args=[self.voting_session.public_token])
        )
        self.assertContains(room_response, "Вперёд")
        self.organizer.post(
            reverse("poker:session_start", args=[self.voting_session.pk])
        )
        vote_url = reverse(
            "poker:room_vote", args=[self.voting_session.public_token]
        )
        navigate_url = reverse(
            "poker:room_navigate", args=[self.voting_session.public_token]
        )
        state_url = reverse(
            "poker:room_state", args=[self.voting_session.public_token]
        )

        self.assertEqual(anna.post(vote_url, {"value": 2}).status_code, 200)
        self.assertEqual(
            anna.post(navigate_url, {"direction": "next"}).status_code, 200
        )
        self.assertEqual(anna.post(vote_url, {"value": 8}).status_code, 200)
        self.assertEqual(boris.post(vote_url, {"value": 4}).status_code, 200)

        anna_state = anna.get(state_url).json()
        boris_state = boris.get(state_url).json()
        self.assertEqual(anna_state["current_task"]["id"], second_task.pk)
        self.assertEqual(anna_state["round"]["my_vote"], 8)
        self.assertEqual(anna_state["queue"]["total"], 2)
        self.assertEqual(anna_state["queue"]["voted"], 2)
        self.assertEqual(boris_state["current_task"]["id"], self.task.pk)
        self.assertEqual(boris_state["round"]["my_vote"], 4)
        self.assertEqual(boris_state["queue"]["voted"], 1)

        organizer_state = self.organizer.get(
            reverse("poker:session_state", args=[self.voting_session.pk])
        ).json()
        self.assertEqual(
            [item["vote_count"] for item in organizer_state["queue_items"]],
            [2, 1],
        )

        anna.post(navigate_url, {"direction": "previous"})
        returned_state = anna.get(state_url).json()
        self.assertEqual(returned_state["current_task"]["id"], self.task.pk)
        self.assertEqual(returned_state["round"]["my_vote"], 2)

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
