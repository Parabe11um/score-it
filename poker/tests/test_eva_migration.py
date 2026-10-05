from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class EvaMigrationTests(TransactionTestCase):
    def test_existing_tasks_votes_capacities_and_assignments_survive(self):
        before = [('poker', '0014_sprint_team_capacity')]
        after = [('poker', '0015_eva_sync_quotas_types')]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        old = executor.loader.project_state(before).apps
        try:
            user = old.get_model('auth', 'User').objects.create(username='migration-owner')
            project = old.get_model('poker', 'Project').objects.create(owner_id=user.pk, name='Old')
            task = old.get_model('poker', 'Task').objects.create(
                project_id=project.pk, number='OLD-1', title='Old task', competency='development',
                imported_estimate=12, estimate_sum=24, estimate_count=2, status='estimated')
            sprint = old.get_model('poker', 'Sprint').objects.create(project_id=project.pk, name='Old', development_capacity=80)
            link = old.get_model('poker', 'SprintTask').objects.create(sprint_id=sprint.pk, task_id=task.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            new = executor.loader.project_state(after).apps
            result = new.get_model('poker','Task').objects.get(pk=task.pk)
            self.assertEqual(result.imported_estimate, Decimal('12.00'))
            self.assertEqual((result.estimate_sum, result.estimate_count), (24, 2))
            self.assertEqual(result.competency, 'development')
            self.assertEqual(result.quota, '')
            result_sprint = new.get_model('poker','Sprint').objects.get(pk=sprint.pk)
            self.assertEqual(result_sprint.development_capacity, 80)
            self.assertIsNone(result_sprint.development_abs_capacity)
            self.assertEqual(new.get_model('poker','SprintTask').objects.get(pk=link.pk).status, 'planned')
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
