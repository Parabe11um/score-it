from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from poker.models import Participant, Project, Sprint, SprintTask, Task, Vote, VotingRound, VotingSession
from poker.sprint_import import available_sprint_tasks, parse_sprint_file, save_sprint_import
from poker.tests.test_sprint_import import row, upload


class EvaZeroTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('zero-owner')
        self.project = Project.objects.create(owner=self.owner, name='ABS')
        self.task = Task.objects.create(project=self.project, number='ABS-SA-1', title='Task',
                                        status='estimated', imported_estimate=20,
                                        estimate_sum=12, estimate_count=1)
        self.session = VotingSession.objects.create(project=self.project, name='History')
        self.round = VotingRound.objects.create(session=self.session, task=self.task, number=1, status='closed')
        self.participant = Participant.objects.create(session=self.session, name='Expert')
        self.vote = Vote.objects.create(voting_round=self.round, participant=self.participant, value=12)
        self.client.force_login(self.owner)

    def sync(self, estimate, **kwargs):
        return save_sprint_import(self.project, parse_sprint_file(upload([row(estimate=estimate, **kwargs)])))

    def test_all_numeric_zeros_clear_current_result_without_deleting_votes(self):
        for zero in (0, 0.0, '0', '0.0', '0,00', '-0.00', '+0', '0e0'):
            Task.objects.filter(pk=self.task.pk).update(status='estimated', imported_estimate=20,
                                                      estimate_sum=12, estimate_count=1)
            self.sync(zero)
            self.task.refresh_from_db()
            self.assertEqual(self.task.status, Task.Status.UNESTIMATED)
            self.assertIsNone(self.task.estimate)
            self.assertIsNone(self.task.imported_estimate)
            self.assertIsNone(self.task.average_estimate)
            self.assertNotIn(self.task, available_sprint_tasks(self.project))
            self.vote.refresh_from_db()
            self.assertEqual(self.vote.value, 12)
            self.assertEqual(self.task.voting_rounds.count(), 1)
            self.assertEqual(self.sync(zero).counts['unchanged'], 1)

    def test_empty_cell_preserves_result_zero_clears_and_positive_restores(self):
        self.sync('')
        self.task.refresh_from_db()
        self.assertEqual(self.task.estimate, 20)
        self.sync(0)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.estimate)
        self.sync('7,5')
        self.task.refresh_from_db()
        self.assertEqual(self.task.estimate, 7.5)
        self.assertEqual(self.task.status, Task.Status.ESTIMATED)
        self.assertEqual(Vote.objects.get(pk=self.vote.pk).value, 12)

    def test_planned_zero_removes_hours_but_keeps_assignment(self):
        sprint = Sprint.objects.create(project=self.project, name='Plan')
        SprintTask.objects.create(sprint=sprint, task=self.task)
        self.sync(0)
        self.assertEqual(Sprint.objects.get(pk=sprint.pk).total_estimate, 0)
        self.assertEqual(self.task.sprint_items.filter(status='planned').count(), 1)
        self.assertEqual(self.client.get(reverse('poker:sprint_export_eva', args=[sprint.pk])).status_code, 302)

    def test_zero_with_closed_task_also_clears_result(self):
        self.sync(0, status='Выполнена')
        self.task.refresh_from_db()
        self.assertIsNone(self.task.estimate)
        self.assertIsNotNone(self.task.completed_at)

    def test_active_voting_is_protected_but_completed_plan_accepts_eva_zero(self):
        self.round.status = 'voting'
        self.round.save()
        self.assertEqual(self.sync(0).counts['voting'], 1)
        self.task.refresh_from_db()
        self.assertEqual(self.task.estimate, 20)
        self.round.status = 'closed'
        self.round.save()
        sprint = Sprint.objects.create(project=self.project, name='Finished', status='completed')
        SprintTask.objects.create(sprint=sprint, task=self.task)
        self.assertEqual(self.sync(0).counts['conflicts'], 0)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.estimate)
        self.assertFalse(self.task.eva_readiness_stale)
        self.assertEqual(self.task.sprint_items.get(status='planned').sprint_id, sprint.pk)
        self.assertEqual(Vote.objects.get(pk=self.vote.pk).value, 12)

    def test_conflicting_blank_and_zero_rows_do_not_choose_arbitrarily(self):
        with self.assertRaisesMessage(ValidationError, 'разными данными'):
            parse_sprint_file(upload([row(estimate=''), row(estimate=0)]))

    def test_zero_import_via_room_queues_task_for_new_estimation(self):
        new_room = VotingSession.objects.create(project=self.project, name='Reestimate')
        response = self.client.post(reverse('poker:session_import_file', args=[new_room.pk]),
                                    {'task_file': upload([row(estimate=0)])}, follow=True)
        self.assertContains(response, 'В очередь добавлено: 1')
        self.assertEqual(new_room.queue_items.get().task_id, self.task.pk)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.estimate)
        self.task.capture_estimate(self.round)
        self.assertEqual(self.task.estimate, 12)
