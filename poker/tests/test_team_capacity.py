from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from poker.models import Project, ProjectMember, Sprint, SprintResource, SprintTask, Task
from poker.team import add_project_members


class TeamCapacityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("team-owner")
        cls.other = get_user_model().objects.create_user("other-team-owner")
        cls.project = Project.objects.create(owner=cls.owner, name="Team project")
        cls.other_project = Project.objects.create(owner=cls.other, name="Other project")
        cls.member = ProjectMember.objects.create(
            project=cls.project, full_name="Тестовый сотрудник", competency="development",
            allocation_percent=75, hours_per_day=8,
        )
        cls.sprint = Sprint.objects.create(
            project=cls.project, name="2026.20", start_date=date(2026, 9, 28),
            end_date=date(2026, 10, 11), capacity_source="team", reserve_percent=20,
            development_capacity=Decimal("123.45"),
        )
        add_project_members(cls.sprint)
        cls.resource = cls.sprint.resources.get()
        cls.resource.absence_days = Decimal("2")
        cls.resource.save()

    def setUp(self):
        self.client.force_login(self.owner)

    def settings(self, **changes):
        values = {
            "capacity_source": "team", "start_date": "2026-09-28",
            "end_date": "2026-10-11", "working_days_override": "", "reserve_percent": "20",
        }
        values.update(changes)
        return values

    def update_resource(self, resource=None, **changes):
        resource = resource or self.resource
        values = {"full_name": resource.full_name, "competency": "development", "allocation_percent": "75",
                  "hours_per_day": "8", "absence_days": "2"}
        values.update(changes)
        return self.client.post(reverse("poker:sprint_resource_update", args=[resource.sprint_id, resource.pk]), values)

    def test_capacity_uses_allocation_absence_and_reserve_once(self):
        self.assertEqual(self.sprint.working_days, 10)
        self.assertEqual(self.sprint.team_gross_capacity, Decimal("48"))
        self.assertEqual(self.sprint.capacity_total, Decimal("38.40"))
        self.assertEqual(self.sprint.team_capacities["analysis"], 0)
        self.assertEqual(self.sprint.team_capacities["development"], Decimal("38.40"))

    def test_workbook_example_preserves_employee_capacity_without_quota_double_count(self):
        self.sprint.resources.all().delete()
        for i in range(10):
            SprintResource.objects.create(
                sprint=self.sprint, full_name=f"Example {i}", competency="development",
                allocation_percent=100, hours_per_day=8, absence_days=5 if i == 0 else 0,
            )
        self.assertEqual(self.sprint.team_gross_capacity, 760)
        self.assertEqual(self.sprint.capacity_total, 608)

    def test_zero_allocation_full_absence_and_zero_reserve_are_valid(self):
        for changes, expected in (({"allocation_percent": "0"}, 0),
                                  ({"absence_days": "10"}, 0),
                                  ({"absence_days": "0.5"}, Decimal("45.60"))):
            with self.subTest(changes=changes):
                self.assertEqual(self.update_resource(**changes).status_code, 302)
                self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, expected)
        self.client.post(reverse("poker:sprint_team", args=[self.sprint.pk]), self.settings(reserve_percent="0"))
        self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, 57)

    def test_weekend_calendar_correction_and_zero_workdays(self):
        self.assertEqual(Sprint(start_date=date(2026, 9, 26), end_date=date(2026, 9, 27)).working_days, 0)
        response = self.client.post(reverse("poker:sprint_team", args=[self.sprint.pk]), self.settings(working_days_override="9"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, Decimal("33.60"))
        self.update_resource(absence_days="0")
        response = self.client.post(reverse("poker:sprint_team", args=[self.sprint.pk]), self.settings(working_days_override="0"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, 0)

    def test_incomplete_calculation_is_unknown_not_zero(self):
        self.sprint.start_date = None
        self.assertIsNone(self.sprint.capacity_total)
        self.assertIsNone(self.sprint.capacity_remaining)
        self.assertEqual(self.sprint.capacity_total_display, "—")
        empty = Sprint.objects.create(project=self.project, name="Empty", capacity_source="team",
                                      start_date=date(2026, 9, 28), end_date=date(2026, 10, 11))
        self.assertIsNone(empty.capacity_total)

    def test_editing_or_archiving_member_does_not_change_sprint_snapshot(self):
        response = self.client.post(reverse("poker:project_member_edit", args=[self.project.pk, self.member.pk]), {
            "full_name": "Новое имя", "competency": "testing", "allocation_percent": "50", "hours_per_day": "4",
        })
        self.assertEqual(response.status_code, 302)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_active)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.full_name, "Тестовый сотрудник")
        self.assertEqual(self.resource.competency, "development")
        self.assertEqual(self.sprint.capacity_total, Decimal("38.40"))

    def test_readding_team_keeps_adjusted_snapshot_and_no_duplicates(self):
        self.member.allocation_percent = 10
        self.member.save()
        self.assertEqual(add_project_members(self.sprint), 0)
        self.assertEqual(self.sprint.resources.count(), 1)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.allocation_percent, 75)
        self.assertEqual(self.resource.absence_days, 2)

    def test_manual_capacity_roundtrip_preserves_existing_values(self):
        url = reverse("poker:sprint_team", args=[self.sprint.pk])
        self.client.post(url, self.settings(capacity_source="manual"))
        self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, Decimal("123.45"))
        self.client.post(url, self.settings())
        self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, Decimal("38.40"))
        legacy = Sprint.objects.create(project=self.project, name="Legacy", capacity=Decimal("200.50"))
        self.assertEqual(legacy.capacity_source, "manual")
        self.assertEqual(legacy.capacity_total, Decimal("200.50"))
        self.assertFalse(legacy.resources.exists())

    def test_manual_endpoint_cannot_overwrite_team_capacity(self):
        self.client.post(reverse("poker:sprint_capacity_update", args=[self.sprint.pk]), {"development_capacity": "999"})
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.development_capacity, Decimal("123.45"))
        self.assertEqual(self.sprint.capacity_total, Decimal("38.40"))

    def test_manual_correction_on_team_page_and_return_to_team_calculation(self):
        url = reverse("poker:sprint_team", args=[self.sprint.pk])
        response = self.client.post(url, {
            "action": "capacity", "analysis_capacity": "0", "development_abs_capacity": "120.50",
            "development_be_capacity": "20", "development_fe_capacity": "30",
            "testing_capacity": "40", "defect_capacity": "10",
        }, follow=True)
        self.assertRedirects(response, url)
        sprint = Sprint.objects.get(pk=self.sprint.pk)
        self.assertEqual(sprint.capacity_source, "manual")
        self.assertEqual(sprint.capacity_total, Decimal("220.50"))
        self.assertEqual(sprint.analysis_capacity, 0)
        self.assertIsNone(sprint.development_capacity)
        self.assertContains(response, 'value="120.50"')
        self.assertEqual(sprint.resources.count(), 1)
        self.client.post(url, self.settings())
        sprint = Sprint.objects.get(pk=self.sprint.pk)
        self.assertEqual(sprint.capacity_total, Decimal("38.40"))
        self.assertEqual(sprint.development_abs_capacity, Decimal("120.50"))

    def test_invalid_manual_correction_preserves_values_and_source(self):
        url = reverse("poker:sprint_team", args=[self.sprint.pk])
        for value in ("-1", "no", "9999999", "1.234"):
            with self.subTest(value=value):
                response = self.client.post(url, {"action": "capacity", "development_capacity": value})
                self.assertEqual(response.status_code, 400)
                sprint = Sprint.objects.get(pk=self.sprint.pk)
                self.assertEqual(sprint.capacity_source, "team")
                self.assertEqual(sprint.development_capacity, Decimal("123.45"))
                self.assertEqual(sprint.capacity_total, Decimal("38.40"))
                self.assertContains(response, 'action="' + url + '"', status_code=400)

    def test_temporary_resource_create_edit_remove_recalculates_only_this_sprint(self):
        other_sprint = Sprint.objects.create(project=self.project, name="Another", capacity_source="team",
                                             start_date=self.sprint.start_date, end_date=self.sprint.end_date)
        add_project_members(other_sprint)
        response = self.client.post(reverse("poker:sprint_resource_create", args=[self.sprint.pk]), {
            "full_name": "  Временный   участник ", "competency": "development_be",
            "allocation_percent": "50", "hours_per_day": "8", "absence_days": "0.5",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        resource = self.sprint.resources.get(member__isnull=True)
        self.assertEqual(resource.full_name, "Временный участник")
        self.assertEqual(resource.capacity_hours, Decimal("30.40"))
        self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, Decimal("68.80"))
        self.assertEqual(self.project.members.count(), 1)
        self.assertEqual(self.update_resource(resource, full_name="Новое имя", competency="testing",
                                              allocation_percent="100", absence_days="0").status_code, 302)
        resource.refresh_from_db()
        self.assertEqual(resource.full_name, "Новое имя")
        self.assertEqual(resource.capacity_hours, 64)
        self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).team_capacities["testing"], 64)
        self.assertEqual(Sprint.objects.get(pk=other_sprint.pk).capacity_total, 60)
        self.client.post(reverse("poker:sprint_resource_remove", args=[self.sprint.pk, resource.pk]))
        self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, Decimal("38.40"))

    def test_invalid_new_resource_keeps_bound_form_and_does_not_create_member(self):
        url = reverse("poker:sprint_resource_create", args=[self.sprint.pk])
        for changes in ({"full_name": " "}, {"absence_days": "11"}, {"allocation_percent": "101"}):
            values = {"full_name": "New", "competency": "analysis", "allocation_percent": "100",
                      "hours_per_day": "8", "absence_days": "0"}
            values.update(changes)
            response = self.client.post(url, values)
            self.assertEqual(response.status_code, 400)
            self.assertTrue(response.context["new_resource_form"].errors)
            self.assertEqual(self.sprint.resources.count(), 1)
            self.assertEqual(self.project.members.count(), 1)

    def test_resource_edit_keeps_project_member_and_other_sprint_snapshot(self):
        other_sprint = Sprint.objects.create(project=self.project, name="Another")
        add_project_members(other_sprint)
        self.assertEqual(self.update_resource(full_name="Исправленное имя", competency="development_abs").status_code, 302)
        self.member.refresh_from_db()
        self.assertEqual(self.member.full_name, "Тестовый сотрудник")
        self.assertEqual(other_sprint.resources.get().full_name, "Тестовый сотрудник")
        response = self.client.post(reverse("poker:sprint_resource_update", args=[self.sprint.pk, other_sprint.resources.get().pk]), {})
        self.assertEqual(response.status_code, 404)

    def test_role_overload_is_not_hidden_by_another_roles_free_hours(self):
        SprintResource.objects.create(sprint=self.sprint, full_name="Analyst", competency="analysis")
        task = Task.objects.create(project=self.project, number="DEV-1", title="Work",
                                   competency="development", status="estimated", imported_estimate=52)
        SprintTask.objects.create(sprint=self.sprint, task=task)
        self.assertGreater(self.sprint.capacity_total, 52)
        self.assertTrue(self.sprint.is_over_capacity)
        self.assertEqual(self.sprint.capacity_overage, Decimal("13.60"))

    def test_new_sprint_uses_current_team_and_excludes_inactive_members(self):
        ProjectMember.objects.create(project=self.project, full_name="Inactive", competency="testing", is_active=False)
        response = self.client.post(reverse("poker:sprint_create", args=[self.project.pk]), {
            "name": "New", "capacity_source": "team", "start_date": "2026-09-28", "end_date": "2026-10-11",
        })
        self.assertEqual(response.status_code, 302)
        new = Sprint.objects.get(name="New")
        self.assertEqual(new.resources.count(), 1)
        self.assertEqual(new.resources.get().absence_days, 0)
        self.assertEqual(new.capacity_total, 60)

    def test_copy_resets_dates_absence_calendar_and_uses_current_member_defaults(self):
        self.member.allocation_percent = 50
        self.member.save()
        self.sprint.working_days_override = 9
        self.sprint.save()
        response = self.client.post(reverse("poker:sprint_copy", args=[self.sprint.pk]))
        self.assertEqual(response.status_code, 302)
        copied = Sprint.objects.exclude(pk=self.sprint.pk).get()
        self.assertIsNone(copied.start_date)
        self.assertIsNone(copied.end_date)
        self.assertIsNone(copied.working_days_override)
        self.assertEqual(copied.reserve_percent, 20)
        self.assertEqual(copied.resources.get().allocation_percent, 50)
        self.assertEqual(copied.resources.get().absence_days, 0)
        self.assertIsNone(copied.capacity_total)
        self.assertFalse(copied.tasks.exists())

    def test_invalid_employee_inputs_do_not_write(self):
        for changes in ({"allocation_percent": "101"}, {"allocation_percent": "-1"},
                        {"hours_per_day": "0"}, {"hours_per_day": "25"},
                        {"absence_days": "-1"}, {"absence_days": "11"}, {"competency": ""}):
            with self.subTest(changes=changes):
                self.assertEqual(self.update_resource(**changes).status_code, 400)
                self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, Decimal("38.40"))

    def test_invalid_calendar_reserve_and_period_changes_do_not_write(self):
        for changes in ({"reserve_percent": "101"}, {"reserve_percent": "-1"},
                        {"working_days_override": "15"}, {"working_days_override": "1"},
                        {"end_date": "2026-09-27"}, {"start_date": ""}):
            with self.subTest(changes=changes):
                response = self.client.post(reverse("poker:sprint_team", args=[self.sprint.pk]), self.settings(**changes))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, Decimal("38.40"))

    def test_completed_and_archived_sprints_reject_all_team_mutations(self):
        for status, archived_at in (("completed", None), ("planning", timezone.now())):
            self.sprint.status, self.sprint.archived_at = status, archived_at
            self.sprint.save()
            self.client.post(reverse("poker:sprint_team", args=[self.sprint.pk]), self.settings(reserve_percent="90"))
            self.client.post(reverse("poker:sprint_members_add", args=[self.sprint.pk]), {"all": "1"})
            self.client.post(reverse("poker:sprint_resource_create", args=[self.sprint.pk]), {
                "full_name": "New", "competency": "analysis", "allocation_percent": "100",
                "hours_per_day": "8", "absence_days": "0",
            })
            self.client.post(reverse("poker:sprint_team", args=[self.sprint.pk]), {
                "action": "capacity", "development_capacity": "999",
            })
            self.update_resource(allocation_percent="0")
            self.client.post(reverse("poker:sprint_resource_remove", args=[self.sprint.pk, self.resource.pk]))
            self.assertEqual(Sprint.objects.get(pk=self.sprint.pk).capacity_total, Decimal("38.40"))
            self.assertEqual(self.sprint.resources.count(), 1)
            response = self.client.get(reverse("poker:sprint_team", args=[self.sprint.pk]))
            self.assertNotContains(response, "Сохранить параметры")

    def test_ownership_and_cross_project_membership_are_enforced(self):
        foreign = ProjectMember.objects.create(project=self.other_project, full_name="Foreign", competency="analysis")
        self.assertEqual(self.client.post(reverse("poker:sprint_members_add", args=[self.sprint.pk]), {"member_id": foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(reverse("poker:project_member_edit", args=[self.project.pk, foreign.pk])).status_code, 404)
        with self.assertRaises(ValidationError):
            SprintResource(sprint=self.sprint, member=foreign, full_name="Foreign", competency="analysis").full_clean()
        self.client.force_login(self.other)
        for name, args in (("project_team", [self.project.pk]), ("sprint_team", [self.sprint.pk]),
                           ("sprint_resource_update", [self.sprint.pk, self.resource.pk]),
                           ("sprint_resource_remove", [self.sprint.pk, self.resource.pk]),
                           ("sprint_resource_create", [self.sprint.pk]),
                           ("sprint_members_add", [self.sprint.pk])):
            self.assertEqual(self.client.post(reverse(f"poker:{name}", args=args), self.settings()).status_code, 404)

    def test_pages_and_export_use_same_capacity_and_protect_spreadsheet_text(self):
        for url in (self.project.get_absolute_url(), self.sprint.get_absolute_url(),
                    reverse("poker:project_team", args=[self.project.pk]),
                    reverse("poker:sprint_team", args=[self.sprint.pk])):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.resource.full_name = "=1+1"
        self.resource.save()
        response = self.client.get(reverse("poker:sprint_export", args=[self.sprint.pk]))
        book = load_workbook(BytesIO(response.content), data_only=False)
        self.assertEqual(book["Команда"]["A2"].data_type, "s")
        self.assertEqual(book["Команда"]["I2"].value, 38.4)
        self.assertEqual(book["Ёмкость"]["C3"].value, 38.4)
        self.assertEqual(book["Команда"]["C2"].value, .75)
