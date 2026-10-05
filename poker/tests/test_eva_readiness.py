from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from poker.eva_readiness import EvaReadiness, title_key
from poker.forms import VotingSessionForm
from poker.models import Project, Sprint, SprintTask, Task, VotingRound, VotingSession, VotingSessionTask
from poker.sprint_import import parse_sprint_file, save_sprint_import
from poker.tests.test_sprint_import import PLANNING_HEADERS, row, upload


class EvaTitleTests(SimpleTestCase):
    def test_only_known_prefixes_and_trailing_qa_environments_are_removed(self):
        for title in ('[SA] Платежи', '[ABS] Платежи', '[BE] Платежи', '[FE] Платежи',
                      '[QA]  Платежи DEV', '[qa] Платежи - TEST', '[QA] Платежи — DEV',
                      '[QA]\u00a0Платежи\u00a0TEST', '  Платежи '):
            self.assertEqual(title_key(title), 'платежи')
        for title, expected in (('[SA] Отчёт DEV', 'отчёт dev'), ('[ABS] Отчёт TEST', 'отчёт test'),
                                ('[QA] TEST внутри DEV', 'test внутри'),
                                ('[QA] Отчёт_DEV', 'отчёт_dev'), ('[OTHER] Отчёт', '[other] отчёт'),
                                ('[QA] Отчёт TEST-2', 'отчёт test-2')):
            self.assertEqual(title_key(title), expected)


class EvaReadinessTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('readiness-owner')
        self.project = Project.objects.create(owner=self.owner, name='Project')
        self.room = VotingSession.objects.create(project=self.project, name='Room')
        self.client.force_login(self.owner)
        self.analysis = self.task('A', '[SA] Платежи', Task.Competency.ANALYSIS, eva_status='Выполнена')
        self.dev = self.task('D', '[ABS] Платежи', Task.Competency.DEVELOPMENT_ABS)

    def task(self, number, title, competency, **kwargs):
        return Task.objects.create(project=self.project, number=number, title=title, competency=competency,
                                   external_url=f'https://example.org/?popup=CmfTask:{uuid4()}', **kwargs)

    def decision(self, task=None):
        return EvaReadiness.for_project(self.project).check(task or self.dev)

    def test_types_require_analysis_but_manual_tasks_analysis_and_defects_do_not(self):
        self.analysis.eva_status = 'В работе'
        self.analysis.save()
        for kind in ('development_abs', 'development_be', 'development_fe', 'testing'):
            self.dev.competency = kind
            self.assertFalse(self.decision().allowed)
        for kind in ('analysis', 'defect', 'development', ''):
            self.dev.competency = kind
            self.assertTrue(self.decision().allowed)
        self.dev.competency = 'development_abs'
        self.dev.external_url = ''
        self.assertTrue(self.decision().allowed)

    def test_explicit_completion_not_estimate_or_local_completion_or_closed_cache(self):
        self.assertTrue(self.decision().allowed)
        for status in ('Открыта', 'В работе', 'Ревью', 'Закрыта', 'Отменена', '', 'Закрыто'):
            self.analysis.eva_status = status
            self.analysis.imported_estimate = 20
            self.analysis.save()
            self.assertFalse(self.decision().allowed)
        self.assertIn('A —', self.decision().message)

    def test_all_matching_analysis_tasks_must_be_done(self):
        other = self.task('A2', '[SA] Платежи', 'analysis', eva_status='Ревью')
        self.assertFalse(self.decision().allowed)
        other.eva_status = 'Выполнена'
        other.save()
        self.assertTrue(self.decision().allowed)

    def test_parent_title_precedes_child_title_and_known_parents_never_mix(self):
        self.analysis.eva_parent_title = 'Функциональность 1'
        self.analysis.title = '[SA] Уточнение требований'
        self.analysis.save()
        self.dev.eva_parent_title = 'Функциональность 1'
        self.dev.save()
        self.assertTrue(self.decision().allowed)
        self.dev.eva_parent_title = 'Функциональность 2'
        self.dev.save()
        self.assertFalse(self.decision().allowed)

    def test_scopes_and_project_boundaries_prevent_false_matches(self):
        self.analysis.eva_epic_title = 'Epic A'
        self.analysis.save()
        self.dev.eva_epic_title = 'Epic B'
        self.dev.save()
        self.assertFalse(self.decision().allowed)
        self.dev.eva_epic_title = ''
        self.dev.save()
        self.assertFalse(self.decision().allowed)
        self.analysis.eva_epic_title = ''
        self.analysis.project = Project.objects.create(owner=self.owner, name='Other project')
        self.analysis.save()
        self.assertFalse(self.decision().allowed)

    def test_missing_or_ambiguous_analysis_is_visible_in_room_and_project(self):
        self.analysis.delete()
        self.assertFalse(self.decision().allowed)
        response = self.client.get(self.room.get_absolute_url())
        self.assertContains(response, 'Ожидают аналитики')
        self.assertContains(response, 'Не найдена аналитика')
        self.assertNotIn(self.dev, response.context['available_tasks'])
        self.assertContains(self.client.get(self.project.get_absolute_url()), 'Не найдена аналитика')
        self.assertNotIn(self.dev, VotingSessionForm(project=self.project).fields['task_ids'].queryset)

    def test_all_queue_entry_points_enforce_readiness_on_the_server(self):
        self.analysis.eva_status = 'Ревью'
        self.analysis.save()
        response = self.client.post(reverse('poker:session_queue_add', args=[self.room.pk]), {'task_ids': [self.dev.pk]}, follow=True)
        self.assertContains(response, 'Ожидает выполнения аналитики')
        self.assertFalse(self.room.queue_items.exists())
        response = self.client.post(reverse('poker:session_start_task', args=[self.room.pk, self.dev.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.room.queue_items.exists())
        response = self.client.post(reverse('poker:session_create', args=[self.project.pk]),
                                    {'name': 'Forged', 'minimum_participants': 1, 'task_ids': [self.dev.pk]})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(VotingSession.objects.filter(name='Forged').exists())
        VotingSessionTask.objects.create(session=self.room, task=self.dev, position=1)
        self.client.post(reverse('poker:session_copy', args=[self.room.pk]))
        copied = VotingSession.objects.exclude(pk=self.room.pk).get()
        self.assertFalse(copied.queue_items.exists())
        self.client.post(reverse('poker:session_start', args=[self.room.pk]))
        self.assertFalse(self.room.rounds.exists())
        self.analysis.eva_status = 'Выполнена'
        self.analysis.save()
        self.client.post(reverse('poker:session_start', args=[self.room.pk]))
        self.assertEqual(self.room.rounds.count(), 1)

    def test_reopened_analysis_blocks_next_round_without_discarding_current_votes(self):
        self.client.post(reverse('poker:session_start_task', args=[self.room.pk, self.dev.pk]))
        voting_round = VotingRound.objects.get(session=self.room)
        voting_round.status = 'revealed'
        voting_round.save()
        self.analysis.eva_status = 'Ревью'
        self.analysis.save()
        self.client.post(reverse('poker:session_revote', args=[self.room.pk]))
        voting_round.refresh_from_db()
        self.assertEqual(voting_round.status, 'revealed')
        self.assertEqual(VotingRound.objects.filter(session=self.room).count(), 1)


class EvaReadinessImportTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('import-readiness')
        self.project = Project.objects.create(owner=self.owner, name='Import')
        self.client.force_login(self.owner)

    def data(self, status='Выполнена', *, parent=False):
        rows = [row('DEV-2', competency='Разработка АБС', title='[ABS] Платежи', estimate=0),
                row('QA-3', competency='Тестирование', title='[QA] Платежи TEST', estimate=0),
                row('SA-1', title='[SA] Платежи', status=status, estimate=12)]
        headers = PLANNING_HEADERS
        if parent:
            headers = (*headers, 'Родительская задача.Наименование', 'Проект.Имя объекта', 'Epic.Наименование')
            rows = [r + ['Платежи', 'ABS', 'Epic'] for r in rows]
        return upload(rows, headers=headers)

    def test_import_order_independent_and_partial_status_update_unlocks_existing_tasks(self):
        room = VotingSession.objects.create(project=self.project, name='Room')
        response = self.client.post(reverse('poker:session_import_file', args=[room.pk]), {'task_file': self.data('Ревью')}, follow=True)
        self.assertContains(response, 'В очередь добавлено: 0')
        self.assertEqual(self.project.tasks.count(), 3)
        self.assertFalse(room.queue_items.exists())
        save_sprint_import(self.project, parse_sprint_file(upload([row('SA-1', title='[SA] Платежи', status='Выполнена')])))
        response = self.client.post(reverse('poker:session_import_file', args=[room.pk]), {'task_file': self.data()}, follow=True)
        self.assertContains(response, 'В очередь добавлено: 2')
        self.assertEqual(set(room.queue_items.values_list('task__number', flat=True)), {'DEV-2', 'QA-3'})
        save_sprint_import(self.project, parse_sprint_file(self.data('Ревью')))
        dev = self.project.tasks.get(number='DEV-2')
        self.assertFalse(EvaReadiness.for_project(self.project).check(dev).allowed)

    def test_optional_relationship_columns_are_preserved_when_missing_cleared_when_empty(self):
        saved = save_sprint_import(self.project, parse_sprint_file(self.data(parent=True)))
        self.assertEqual(len(saved.tasks), 2)
        save_sprint_import(self.project, parse_sprint_file(self.data()))
        dev = self.project.tasks.get(number='DEV-2')
        self.assertEqual((dev.eva_parent_title, dev.eva_project_name, dev.eva_epic_title), ('Платежи', 'ABS', 'Epic'))
        headers = (*PLANNING_HEADERS, 'Родительская задача.Наименование', 'Проект.Имя объекта', 'Epic.Наименование')
        save_sprint_import(self.project, parse_sprint_file(upload([row('DEV-2', competency='Разработка АБС', title='[ABS] Платежи') + ['', '', '']], headers=headers)))
        dev.refresh_from_db()
        self.assertEqual((dev.eva_parent_title, dev.eva_project_name, dev.eva_epic_title), ('', '', ''))
        self.assertFalse(EvaReadiness.for_project(self.project).check(dev).allowed)

    def test_conflicting_duplicate_parent_data_is_not_silently_merged(self):
        headers = (*PLANNING_HEADERS, 'Родительская задача.Наименование')
        with self.assertRaises(ValidationError):
            parse_sprint_file(upload([row() + ['One'], row() + ['Two']], headers=headers))

    def test_skipped_analysis_does_not_leave_a_stale_completed_status_as_permission(self):
        save_sprint_import(self.project, parse_sprint_file(self.data()))
        analysis = self.project.tasks.get(number='SA-1')
        dev = self.project.tasks.get(number='DEV-2')
        finished = Sprint.objects.create(project=self.project, name='History', status='completed')
        SprintTask.objects.create(sprint=finished, task=analysis)
        saved = save_sprint_import(self.project, parse_sprint_file(self.data('Ревью')))
        self.assertEqual(saved.counts['conflicts'], 1)
        analysis.refresh_from_db()
        self.assertEqual(analysis.eva_status, 'Выполнена')
        self.assertEqual(analysis.imported_estimate, 12)
        self.assertFalse(EvaReadiness.for_project(self.project).check(dev).allowed)
        self.assertIn('Не обновлены данные аналитики', EvaReadiness.for_project(self.project).check(dev).message)
        finished.status = 'planning'
        finished.save()
        save_sprint_import(self.project, parse_sprint_file(self.data()))
        self.assertTrue(EvaReadiness.for_project(self.project).check(dev).allowed)

    def test_failed_task_import_requires_a_successful_retry_with_status(self):
        save_sprint_import(self.project, parse_sprint_file(self.data()))
        save_sprint_import(self.project, parse_sprint_file(upload([
            row('DEV-2', competency='Разработка АБС', title='[ABS] Платежи', estimate='invalid')
        ])))
        dev = self.project.tasks.get(number='DEV-2')
        self.assertFalse(EvaReadiness.for_project(self.project).check(dev).allowed)
        save_sprint_import(self.project, parse_sprint_file(self.data()))
        dev.refresh_from_db()
        self.assertTrue(EvaReadiness.for_project(self.project).check(dev).allowed)
