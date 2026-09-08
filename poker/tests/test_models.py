from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from poker.models import (
    ESTIMATION_VALUES,
    Participant,
    Project,
    Task,
    Vote,
    VotingRound,
    VotingSession,
    estimate_on_scale,
)


class EstimateScaleTests(SimpleTestCase):
    def test_nearest_card_and_all_midpoint_ties(self):
        cases = (
            (5, 1, 4),
            (18, 1, 20),
            (25, 1, 20),
            (29, 1, 32),
            (44, 1, 52),
            (201, 10, 20),
            (3, 2, 2),
            (3, 1, 4),
            (6, 1, 8),
            (10, 1, 12),
            (16, 1, 20),
            (26, 1, 32),
            (42, 1, 52),
        )
        for total, count, expected in cases:
            with self.subTest(total=total, count=count):
                self.assertEqual(estimate_on_scale(total, count), expected)

    def test_cards_already_on_scale_do_not_change(self):
        for value in ESTIMATION_VALUES:
            with self.subTest(value=value):
                self.assertEqual(estimate_on_scale(value * 3, 3), value)

    def test_empty_votes_zero_and_small_positive_averages_are_distinct(self):
        for total, count in ((None, None), (None, 2), (0, 0), (0, None)):
            with self.subTest(total=total, count=count):
                self.assertIsNone(estimate_on_scale(total, count))
        self.assertEqual(estimate_on_scale(0, 3), 0)
        self.assertEqual(estimate_on_scale(1, 2), 1)
        self.assertEqual(estimate_on_scale(1, 1000), 1)

    def test_display_rounding_does_not_change_the_nearest_card(self):
        # Both means display as 6.00, but only the second rounds up to 8.
        self.assertEqual(estimate_on_scale(5999, 1000), 4)
        self.assertEqual(estimate_on_scale(6001, 1000), 8)
        self.assertEqual(estimate_on_scale(25999, 1000), 20)
        self.assertEqual(estimate_on_scale(26001, 1000), 32)


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
        self.assertEqual(self.task.average_estimate, Decimal(14) / Decimal(3))
        self.assertEqual(self.task.average_estimate_display, "4.67")
        self.assertEqual(self.task.estimate, 4)
        self.assertEqual(self.task.estimate_display, "4")
        self.assertEqual(self.task.status, Task.Status.ESTIMATED)
        self.assertEqual(self.voting_round.summary()["final_estimate"], 4)
        self.assertEqual(self.voting_round.summary()["values"], [2, 4, 8])

    def test_existing_saved_estimate_uses_scale_without_rewriting_data(self):
        Task.objects.filter(pk=self.task.pk).update(
            estimate_sum=116, estimate_count=4, status=Task.Status.ESTIMATED
        )
        before = Task.objects.filter(pk=self.task.pk).values().get()
        self.task.refresh_from_db()

        self.assertEqual(self.task.average_estimate_display, "29")
        self.assertEqual(self.task.estimate_display, "32")
        self.assertEqual(Task.objects.filter(pk=self.task.pk).values().get(), before)

    def test_no_votes_are_unestimated_and_cannot_be_accepted(self):
        self.assertIsNone(self.task.estimate)
        self.assertEqual(self.task.estimate_display, "—")
        self.assertIsNone(self.task.average_estimate)
        self.assertIsNone(self.voting_round.summary()["average"])
        self.assertIsNone(self.voting_round.summary()["final_estimate"])
        with self.assertRaises(ValueError):
            self.task.capture_estimate(self.voting_round)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.UNESTIMATED)

    def test_hour_estimation_scale_has_exact_values(self):
        self.assertEqual(ESTIMATION_VALUES, (0, 1, 2, 4, 8, 12, 20, 32, 52))
