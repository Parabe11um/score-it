"""Conservative, exact-name matching for EVA estimation prerequisites.

This is a heuristic over the last imported data, not an EVA parent ID relation.
Do not fuzzy-match titles or infer completion from estimates/cache status types.
"""

import re
from collections import defaultdict
from dataclasses import dataclass

from .models import Task


GATED_COMPETENCIES = {
    Task.Competency.DEVELOPMENT_ABS, Task.Competency.DEVELOPMENT_BE,
    Task.Competency.DEVELOPMENT_FE, Task.Competency.TESTING,
}
DONE_STATUSES = {"выполнена", "выполнено", "выполнен", "завершена", "завершено", "завершен", "done", "completed"}
PREFIX = re.compile(r"^\[(SA|ABS|BE|FE|QA)\]\s*", re.IGNORECASE)
# Only QA suffixes are environments. Keep meaningful DEV/TEST in other titles.
ENVIRONMENT = re.compile(r"\s+(?:[-–—]\s*)?(?:DEV|TEST)$", re.IGNORECASE)
READINESS_FIELDS = ("pk", "project_id", "title", "number", "competency", "external_url",
                    "eva_status", "eva_parent_title", "eva_project_name", "eva_epic_title", "completed_at", "eva_readiness_stale")


def normalise(value):
    return " ".join(value.split()).casefold()


def title_key(title):
    title = " ".join(title.split())
    prefix = PREFIX.match(title)
    if prefix:
        title = title[prefix.end():]
        if prefix.group(1).upper() == "QA":
            title = ENVIRONMENT.sub("", title)
    return normalise(title)


def relationship_key(task):
    return normalise(task.eva_parent_title) or title_key(task.title)


@dataclass(frozen=True)
class Readiness:
    allowed: bool = True
    message: str = ""


class EvaReadiness:
    """One project snapshot per operation, without a query per task."""

    def __init__(self, tasks):
        self.groups = defaultdict(list)
        for task in tasks:
            if not task.eva_identifier:
                continue
            key = (task.project_id, relationship_key(task))
            self.groups[key].append(task)

    @classmethod
    def for_project(cls, project):
        return cls(project.tasks.only(*READINESS_FIELDS))

    def check(self, task):
        if not task.eva_identifier or task.competency not in GATED_COMPETENCIES:
            return Readiness()
        if task.eva_readiness_stale:
            return Readiness(False, "Последний импорт задачи не применён. Устраните причину пропуска и повторите импорт со статусами EVA.")
        name = relationship_key(task)
        if not name:
            return Readiness(False, "Нет названия для сопоставления с аналитикой.")
        key = (task.project_id, name)
        # A source project/epic narrows the match; never bridge known conflicts.
        group = self.groups.get(key, [])
        for field in ("eva_project_name", "eva_epic_title"):
            own = normalise(getattr(task, field))
            scopes = {normalise(getattr(item, field)) for item in group}
            if not own and len(scopes - {""}) > 1:
                return Readiness(False, "Неоднозначное название: совпадения в разных проектах или эпиках EVA.")
            group = [item for item in group if not own or not getattr(item, field) or normalise(getattr(item, field)) == own]
        analysis = [item for item in group if item.competency == Task.Competency.ANALYSIS]
        if not analysis:
            return Readiness(False, "Не найдена аналитика с тем же названием родителя или задачи. Импортируйте её вместе со статусом.")
        # Missing scope on either side must not silently join unrelated groups.
        if any(normalise(getattr(item, field)) != normalise(getattr(task, field))
               for item in analysis for field in ("eva_project_name", "eva_epic_title")):
            return Readiness(False, "Для сопоставления не хватает проекта или эпика EVA. Повторите импорт всех полей.")
        stale = [item.number for item in analysis if item.eva_readiness_stale]
        if stale:
            return Readiness(False, "Не обновлены данные аналитики: " + ", ".join(stale) + ". Устраните причину пропуска и повторите импорт со статусами EVA.")
        pending = [item for item in analysis if normalise(item.eva_status) not in DONE_STATUSES]
        if pending:
            labels = ", ".join(f"{item.number} — {item.eva_status or 'нет статуса EVA'}" for item in pending)
            return Readiness(False, f"Ожидает выполнения аналитики: {labels}.")
        return Readiness(True, "Аналитика выполнена: " + ", ".join(item.number for item in analysis) + ". Сопоставлено по названию.")


def estimation_tasks(project):
    """Keep a QuerySet for ModelMultipleChoiceField's server-side validation."""
    tasks = list(project.tasks.only(*READINESS_FIELDS))
    readiness = EvaReadiness(tasks)
    return project.tasks.filter(pk__in=[task.pk for task in tasks if not task.completed_at and readiness.check(task).allowed])
