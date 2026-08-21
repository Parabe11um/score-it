from django.contrib.auth import get_user_model
from django.test import TestCase

from poker.models import Project, Task, Vote, VotingRound, VotingSession, Participant


class TaskEstimateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("organizer", password="secret")
        self.project = Project.objects.create(owner=self.user, name="ABS Core")
        self.task = Task.objects.create(
            project=self.project, number="ABS-1", title="Проверить среднее"
        )
        self.session = VotingSession.objects.create(
            project=self.project, name="Оценка"
        )
        self.voting_round = VotingRound.objects.create(
            session=self.session, task=self.task
        )

    def test_estimate_is_stored_as_sum_and_count(self):
        for index, value in enumerate((2, 4, 8), start=1):
            participant = Participant.objects.create(
                session=self.session, name=f"Участник {index}"
            )
            Vote.objects.create(
                voting_round=self.voting_round, participant=participant, value=value
            )

        self.task.capture_estimate(self.voting_round)
        self.task.refresh_from_db()

        self.assertEqual(self.task.estimate_sum, 14)
        self.assertEqual(self.task.estimate_count, 3)
        self.assertEqual(self.task.estimate_display, "4.67")
        self.assertEqual(self.task.status, Task.Status.ESTIMATED)

