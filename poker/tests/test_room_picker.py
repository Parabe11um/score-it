from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from poker.forms import VotingSessionForm
from poker.models import Project, Sprint, SprintTask, Task, VotingSession


class RoomPickerTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('picker-owner')
        self.project = Project.objects.create(owner=self.owner, name='Picker')
        self.client.force_login(self.owner)
        self.ready = self.task('READY', competency='analysis')
        self.estimated = self.task('ESTIMATED', status='estimated', imported_estimate=12)
        self.local_zero = self.task('ZERO', status='estimated', estimate_sum=0, estimate_count=2)
        self.inconsistent = self.task('OLD', imported_estimate=8)
        self.completed = self.task('DONE', completed_at=timezone.now())
        self.planned = self.task('PLAN')
        SprintTask.objects.create(sprint=Sprint.objects.create(project=self.project, name='Plan'), task=self.planned)
        self.blocked = self.task('BLOCKED', competency='development_be',
                                 external_url=f'https://example.org/?popup=CmfTask:{uuid4()}')
        self.room = VotingSession.objects.create(project=self.project, name='Room')

    def task(self, number, **kwargs):
        return Task.objects.create(project=self.project, number=number, title=number, **kwargs)

    def create_data(self, task):
        return {'name': 'New room', 'minimum_participants': 1, 'task_ids': [task.pk]}

    def test_creation_and_existing_room_offer_only_ready_unestimated_unplanned_tasks(self):
        form = VotingSessionForm(project=self.project)
        self.assertEqual(list(form.fields['task_ids'].queryset), [self.ready])
        self.assertEqual(list(form.initial['task_ids']), [self.ready])
        response = self.client.get(self.room.get_absolute_url())
        self.assertEqual(response.context['available_tasks'], [self.ready])
        self.assertEqual(response.context['waiting_tasks'], [self.blocked])

    def test_submitted_estimated_blocked_and_planned_tasks_cannot_enter_via_picker(self):
        for task in (self.estimated, self.local_zero, self.inconsistent, self.completed, self.planned, self.blocked):
            response = self.client.post(reverse('poker:session_create', args=[self.project.pk]), self.create_data(task))
            self.assertEqual(response.status_code, 400)
        self.assertFalse(VotingSession.objects.filter(name='New room').exists())
        response = self.client.post(reverse('poker:session_queue_add', args=[self.room.pk]),
                                    {'task_ids': list(self.project.tasks.values_list('pk', flat=True))})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(self.room.queue_items.values_list('task_id', flat=True)), [self.ready.pk])

    def test_stale_form_is_revalidated_after_task_gets_an_estimate(self):
        self.client.get(self.project.get_absolute_url())
        self.ready.status = 'estimated'
        self.ready.estimate_sum = 12
        self.ready.estimate_count = 1
        self.ready.save()
        response = self.client.post(reverse('poker:session_create', args=[self.project.pk]), self.create_data(self.ready))
        self.assertEqual(response.status_code, 400)

    def test_competency_values_render_and_backlog_filter_does_not_disable_room_creation(self):
        response = self.client.get(self.project.get_absolute_url(), {'competency': 'testing'})
        content = response.content.decode().split('data-task-visible-selection>', 1)[1].split('</form>', 1)[0]
        self.assertIn('data-competency="analysis"', content)
        self.assertIn('<option value="development_abs">', content)
        self.assertIn('<option value="development_be">', content)
        self.assertIn('<option value="development_fe">', content)
        self.assertIn('<option value="">Без типа</option>', content)
        self.assertNotIn('data-task-submit disabled', content)
        self.assertNotIn(f'name="task_ids" value="{self.estimated.pk}"', content)

    def test_empty_candidate_list_explains_why_and_disables_submit(self):
        self.ready.delete()
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, 'Нет неоценённых задач, доступных для оценки.')
        self.assertContains(response, 'data-task-submit disabled')
