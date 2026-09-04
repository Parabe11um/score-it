import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import cached_property

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone


ESTIMATION_GUIDE = (
    {
        "value": 0,
        "label": "Нет работы",
        "description": "Уже сделано или изменение не требуется.",
    },
    {
        "value": 1,
        "label": "1 час",
        "description": "Очень небольшая правка или быстрая проверка.",
    },
    {
        "value": 2,
        "label": "2 часа",
        "description": "Локальная понятная работа без существенных зависимостей.",
    },
    {
        "value": 4,
        "label": "Полдня",
        "description": "До половины рабочего дня на выполнение и проверку.",
    },
    {
        "value": 8,
        "label": "1 рабочий день",
        "description": "Один полный рабочий день одного специалиста.",
    },
    {
        "value": 12,
        "label": "1,5 рабочих дня",
        "description": "Около полутора рабочих дней одного специалиста.",
    },
    {
        "value": 20,
        "label": "2,5 рабочих дня",
        "description": "Несколько этапов работы общей длительностью около 20 часов.",
    },
    {
        "value": 32,
        "label": "4 рабочих дня",
        "description": "Крупная задача примерно на четыре рабочих дня.",
    },
    {
        "value": 52,
        "label": "6,5 рабочих дней",
        "description": "Очень крупная задача; стоит проверить возможность декомпозиции.",
    },
)
ESTIMATION_VALUES = tuple(item["value"] for item in ESTIMATION_GUIDE)
ESTIMATION_CHOICES = tuple((value, str(value)) for value in ESTIMATION_VALUES)


def default_organizer_invitation_expiry():
    return timezone.now() + timedelta(days=7)


class OrganizerInvitation(models.Model):
    recipient_label = models.CharField(
        "Для кого",
        max_length=160,
        blank=True,
        help_text="Необязательная пометка, видимая только в админке.",
    )
    token = models.UUIDField(
        "Токен",
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Создал",
        related_name="created_organizer_invitations",
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    expires_at = models.DateTimeField(
        "Действует до",
        default=default_organizer_invitation_expiry,
        editable=False,
    )
    used_at = models.DateTimeField("Использовано", null=True, blank=True)
    used_by = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="Созданный организатор",
        related_name="organizer_invitation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Приглашение организатора"
        verbose_name_plural = "Приглашения организаторов"

    def __str__(self):
        return self.recipient_label or f"Приглашение {self.token}"

    @property
    def is_available(self):
        return self.used_at is None and self.expires_at > timezone.now()

    @property
    def status(self):
        if self.used_at is not None:
            return "used"
        if self.expires_at <= timezone.now():
            return "expired"
        return "active"

    def get_absolute_url(self):
        return reverse("poker:organizer_invitation_accept", args=[self.token])


class Project(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="poker_projects",
        verbose_name="Организатор",
    )
    name = models.CharField("Название", max_length=160)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Изменён", auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("poker:project_detail", args=[self.pk])


class Task(models.Model):
    class Competency(models.TextChoices):
        NONE = "", "Без типа"
        ANALYSIS = "analysis", "Аналитика"
        DEVELOPMENT = "development", "Разработка"
        TESTING = "testing", "Тестирование"

    class Status(models.TextChoices):
        UNESTIMATED = "unestimated", "Не оценена"
        ESTIMATED = "estimated", "Оценена"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="tasks",
        verbose_name="Проект",
    )
    number = models.CharField("Номер", max_length=80)
    title = models.CharField("Название", max_length=500)
    competency = models.CharField(
        "Тип задачи",
        max_length=20,
        choices=Competency.choices,
        default=Competency.NONE,
        blank=True,
    )
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.UNESTIMATED
    )
    estimate_sum = models.PositiveIntegerField(
        "Сумма оценок, часы", null=True, blank=True
    )
    estimate_count = models.PositiveIntegerField(
        "Количество голосов", null=True, blank=True
    )
    completed_at = models.DateTimeField("Завершена", null=True, blank=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Изменена", auto_now=True)

    class Meta:
        ordering = ("created_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("project", "number"), name="unique_task_number_in_project"
            )
        ]
        verbose_name = "Задача"
        verbose_name_plural = "Задачи"

    def __str__(self):
        return f"{self.number} — {self.title}"

    @property
    def estimate(self):
        if self.estimate_sum is None or not self.estimate_count:
            return None
        return Decimal(self.estimate_sum) / Decimal(self.estimate_count)

    @property
    def estimate_display(self):
        value = self.estimate
        if value is None:
            return "—"
        rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return format(rounded.normalize(), "f")

    def capture_estimate(self, voting_round):
        summary = voting_round.summary()
        if not summary["count"]:
            raise ValueError("Нельзя сохранить оценку без голосов")
        self.estimate_sum = summary["sum"]
        self.estimate_count = summary["count"]
        self.status = self.Status.ESTIMATED
        self.save(
            update_fields=("estimate_sum", "estimate_count", "status", "updated_at")
        )


class VotingSession(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Подготовка"
        ACTIVE = "active", "Идёт голосование"
        FINISHED = "finished", "Завершена"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="voting_sessions",
        verbose_name="Проект",
    )
    name = models.CharField("Название", max_length=160)
    public_token = models.UUIDField(
        "Публичный токен", default=uuid.uuid4, unique=True, editable=False
    )
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    minimum_participants = models.PositiveSmallIntegerField(
        "Минимум голосов",
        default=2,
        validators=(MinValueValidator(1), MaxValueValidator(100)),
        help_text="Сколько участников должны проголосовать до раскрытия карт.",
    )
    current_task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        related_name="active_in_sessions",
        null=True,
        blank=True,
        verbose_name="Текущая задача",
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    finished_at = models.DateTimeField("Завершена", null=True, blank=True)
    archived_at = models.DateTimeField("В архиве", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Сессия голосования"
        verbose_name_plural = "Сессии голосования"

    def __str__(self):
        return f"{self.project}: {self.name}"

    def get_absolute_url(self):
        return reverse("poker:session_manage", args=[self.pk])

    def get_public_url(self):
        return reverse("poker:room", args=[self.public_token])

    @property
    def current_round(self):
        queue_item = (
            self.queue_items.select_related("current_round")
            .filter(task_id=self.current_task_id)
            .first()
        )
        if queue_item and queue_item.current_round_id:
            if queue_item.current_round.status in (
                VotingRound.Status.VOTING,
                VotingRound.Status.REVEALED,
            ):
                return queue_item.current_round
        return self.rounds.filter(
            task=self.current_task,
            status__in=(VotingRound.Status.VOTING, VotingRound.Status.REVEALED),
        ).first()


class VotingSessionTask(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "В очереди"
        ACTIVE = "active", "Оценивается"
        COMPLETED = "completed", "Оценена"
        SKIPPED = "skipped", "Пропущена"

    session = models.ForeignKey(
        VotingSession,
        on_delete=models.CASCADE,
        related_name="queue_items",
        verbose_name="Сессия",
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="session_queue_items",
        verbose_name="Задача",
    )
    current_round = models.OneToOneField(
        "VotingRound",
        on_delete=models.SET_NULL,
        related_name="queue_item",
        null=True,
        blank=True,
        verbose_name="Текущий раунд",
    )
    position = models.PositiveIntegerField("Порядок")
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.PENDING
    )
    added_at = models.DateTimeField("Добавлена", auto_now_add=True)
    completed_at = models.DateTimeField("Оценена", null=True, blank=True)

    class Meta:
        ordering = ("position", "added_at")
        constraints = [
            models.UniqueConstraint(
                fields=("session", "task"), name="unique_task_in_voting_queue"
            ),
            models.UniqueConstraint(
                fields=("session", "position"),
                name="unique_position_in_voting_queue",
            ),
        ]
        verbose_name = "Задача в очереди голосования"
        verbose_name_plural = "Очередь голосования"

    def __str__(self):
        return f"{self.session.name}: {self.position}. {self.task.number}"


class Participant(models.Model):
    session = models.ForeignKey(
        VotingSession,
        on_delete=models.CASCADE,
        related_name="participants",
        verbose_name="Сессия",
    )
    client_token = models.UUIDField(
        "Токен участника", default=uuid.uuid4, unique=True, editable=False
    )
    name = models.CharField("Имя", max_length=100)
    current_task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        related_name="participant_cursors",
        null=True,
        blank=True,
        verbose_name="Текущая задача участника",
    )
    joined_at = models.DateTimeField("Подключился", auto_now_add=True)
    last_seen_at = models.DateTimeField("Последняя активность", auto_now=True)
    completed_at = models.DateTimeField(
        "Завершил оценку", null=True, blank=True
    )

    class Meta:
        ordering = ("joined_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("session", "name"), name="unique_participant_name_in_session"
            )
        ]
        verbose_name = "Участник"
        verbose_name_plural = "Участники"

    def __str__(self):
        return f"{self.name} ({self.session.name})"


class VotingRound(models.Model):
    class Status(models.TextChoices):
        VOTING = "voting", "Голосование"
        REVEALED = "revealed", "Голоса раскрыты"
        CLOSED = "closed", "Оценка принята"
        CANCELLED = "cancelled", "Отменён"

    session = models.ForeignKey(
        VotingSession,
        on_delete=models.CASCADE,
        related_name="rounds",
        verbose_name="Сессия",
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="voting_rounds",
        verbose_name="Задача",
    )
    number = models.PositiveIntegerField("Номер раунда", default=1)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.VOTING
    )
    created_at = models.DateTimeField("Начат", auto_now_add=True)
    revealed_at = models.DateTimeField("Раскрыт", null=True, blank=True)
    closed_at = models.DateTimeField("Принят", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("session", "task", "number"),
                name="unique_round_number_for_task",
            )
        ]
        verbose_name = "Раунд голосования"
        verbose_name_plural = "Раунды голосования"

    def __str__(self):
        return f"{self.task.number}, раунд {self.number}"

    def summary(self):
        values = list(self.votes.values_list("value", flat=True))
        total = sum(values)
        count = len(values)
        average = Decimal(total) / Decimal(count) if count else None
        return {"values": values, "sum": total, "count": count, "average": average}


class Vote(models.Model):
    voting_round = models.ForeignKey(
        VotingRound,
        on_delete=models.CASCADE,
        related_name="votes",
        verbose_name="Раунд",
    )
    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="votes",
        verbose_name="Участник",
    )
    value = models.PositiveSmallIntegerField(
        "Оценка, часы", choices=ESTIMATION_CHOICES
    )
    created_at = models.DateTimeField("Проголосовал", auto_now_add=True)
    updated_at = models.DateTimeField("Изменил голос", auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("voting_round", "participant"),
                name="one_vote_per_participant_and_round",
            )
        ]
        verbose_name = "Голос"
        verbose_name_plural = "Голоса"

    def __str__(self):
        return f"{self.participant.name}: {self.value}"


class Sprint(models.Model):
    class Status(models.TextChoices):
        PLANNING = "planning", "Планируется"
        ACTIVE = "active", "Активен"
        COMPLETED = "completed", "Завершён"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sprints",
        verbose_name="Проект",
    )
    name = models.CharField("Название", max_length=160)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.PLANNING
    )
    goal = models.CharField("Цель спринта", max_length=500, blank=True)
    start_date = models.DateField("Дата начала", null=True, blank=True)
    end_date = models.DateField("Дата завершения", null=True, blank=True)
    capacity = models.DecimalField(
        "Плановая ёмкость, часы",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    analysis_capacity = models.DecimalField(
        "Ёмкость аналитики, часы",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=(MinValueValidator(Decimal("0")),),
    )
    development_capacity = models.DecimalField(
        "Ёмкость разработки, часы",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=(MinValueValidator(Decimal("0")),),
    )
    testing_capacity = models.DecimalField(
        "Ёмкость тестирования, часы",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=(MinValueValidator(Decimal("0")),),
    )
    tasks = models.ManyToManyField(
        Task,
        through="SprintTask",
        through_fields=("sprint", "task"),
        related_name="sprints",
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    archived_at = models.DateTimeField("В архиве", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Спринт"
        verbose_name_plural = "Спринты"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("poker:sprint_detail", args=[self.pk])

    @cached_property
    def total_estimate(self):
        total = Decimal("0")
        for item in self.sprint_tasks.select_related("task").filter(
            status=SprintTask.Status.PLANNED
        ):
            if item.task.estimate is not None:
                total += item.task.estimate
        return total

    @property
    def total_estimate_display(self):
        rounded = self.total_estimate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return format(rounded.normalize(), "f")

    @staticmethod
    def _display_decimal(value):
        if value is None:
            return "—"
        return format(
            value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize(),
            "f",
        )

    @cached_property
    def estimates_by_competency(self):
        estimates = {
            Task.Competency.ANALYSIS: Decimal("0"),
            Task.Competency.DEVELOPMENT: Decimal("0"),
            Task.Competency.TESTING: Decimal("0"),
            Task.Competency.NONE: Decimal("0"),
        }
        for item in self.sprint_tasks.select_related("task").filter(
            status=SprintTask.Status.PLANNED
        ):
            if item.task.estimate is not None:
                estimates[item.task.competency] += item.task.estimate
        return estimates

    @property
    def uses_competency_capacities(self):
        return any(
            value is not None
            for value in (
                self.analysis_capacity,
                self.development_capacity,
                self.testing_capacity,
            )
        )

    @cached_property
    def competency_capacity_rows(self):
        definitions = (
            (
                Task.Competency.ANALYSIS,
                "Аналитика",
                "analysis",
                self.analysis_capacity,
            ),
            (
                Task.Competency.DEVELOPMENT,
                "Разработка",
                "development",
                self.development_capacity,
            ),
            (
                Task.Competency.TESTING,
                "Тестирование",
                "testing",
                self.testing_capacity,
            ),
        )
        rows = []
        for competency, label, css_class, capacity in definitions:
            estimate = self.estimates_by_competency[competency]
            remaining = (
                max(capacity - estimate, Decimal("0"))
                if capacity is not None
                else None
            )
            overage = (
                max(estimate - capacity, Decimal("0"))
                if capacity is not None
                else Decimal("0")
            )
            if capacity is None:
                usage_percent = 0
            elif capacity <= 0:
                usage_percent = 100 if estimate > 0 else 0
            else:
                usage = (estimate / capacity) * Decimal("100")
                usage_percent = min(
                    int(usage.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                    100,
                )
            rows.append(
                {
                    "key": competency,
                    "label": label,
                    "css_class": css_class,
                    "estimate": estimate,
                    "estimate_display": self._display_decimal(estimate),
                    "capacity": capacity,
                    "capacity_display": self._display_decimal(capacity),
                    "remaining": remaining,
                    "remaining_display": self._display_decimal(remaining),
                    "overage": overage,
                    "overage_display": self._display_decimal(overage),
                    "usage_percent": usage_percent,
                    "is_configured": capacity is not None,
                    "is_over_capacity": capacity is not None and estimate > capacity,
                }
            )
        return rows

    @property
    def untyped_estimate(self):
        return self.estimates_by_competency[Task.Competency.NONE]

    @property
    def untyped_estimate_display(self):
        return self._display_decimal(self.untyped_estimate)

    @property
    def capacity_total(self):
        if self.uses_competency_capacities:
            return sum(
                (
                    row["capacity"]
                    for row in self.competency_capacity_rows
                    if row["capacity"] is not None
                ),
                Decimal("0"),
            )
        return self.capacity

    @property
    def capacity_total_display(self):
        return self._display_decimal(self.capacity_total)

    @property
    def has_capacity(self):
        return self.capacity_total is not None

    @property
    def active_task_count(self):
        return self.sprint_tasks.filter(status=SprintTask.Status.PLANNED).count()

    @property
    def capacity_remaining(self):
        if self.uses_competency_capacities:
            return sum(
                (
                    row["remaining"]
                    for row in self.competency_capacity_rows
                    if row["remaining"] is not None
                ),
                Decimal("0"),
            )
        if self.capacity is None:
            return None
        return max(self.capacity - self.total_estimate, Decimal("0"))

    @property
    def capacity_overage(self):
        if self.uses_competency_capacities:
            return sum(
                (row["overage"] for row in self.competency_capacity_rows),
                Decimal("0"),
            )
        if self.capacity is None:
            return Decimal("0")
        return max(self.total_estimate - self.capacity, Decimal("0"))

    @property
    def capacity_remaining_display(self):
        if self.capacity_remaining is None:
            return "—"
        return format(
            self.capacity_remaining.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ).normalize(),
            "f",
        )

    @property
    def capacity_overage_display(self):
        return format(
            self.capacity_overage.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ).normalize(),
            "f",
        )

    @property
    def capacity_usage_percent(self):
        if self.uses_competency_capacities:
            configured = [
                row
                for row in self.competency_capacity_rows
                if row["is_configured"]
            ]
            total_capacity = sum(
                (row["capacity"] for row in configured), Decimal("0")
            )
            total_estimate = sum(
                (row["estimate"] for row in configured), Decimal("0")
            )
            if total_capacity <= 0:
                return 100 if total_estimate > 0 else 0
            percent = (total_estimate / total_capacity) * Decimal("100")
            return min(
                int(percent.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                100,
            )
        if self.capacity is None:
            return 0
        if self.capacity <= 0:
            return 100 if self.total_estimate > 0 else 0
        percent = (self.total_estimate / self.capacity) * Decimal("100")
        return min(int(percent.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), 100)

    @property
    def is_over_capacity(self):
        if self.uses_competency_capacities:
            return any(
                row["is_over_capacity"]
                for row in self.competency_capacity_rows
            )
        return self.capacity is not None and self.total_estimate > self.capacity

    @property
    def over_capacity_labels(self):
        if not self.uses_competency_capacities:
            return []
        return [
            row["label"]
            for row in self.competency_capacity_rows
            if row["is_over_capacity"]
        ]

    @property
    def unconfigured_capacity_rows(self):
        if not self.uses_competency_capacities:
            return []
        return [
            row
            for row in self.competency_capacity_rows
            if not row["is_configured"] and row["estimate"] > 0
        ]


class SprintTask(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Запланирована"
        TRANSFERRED = "transferred", "Перенесена"

    sprint = models.ForeignKey(
        Sprint,
        on_delete=models.CASCADE,
        related_name="sprint_tasks",
        verbose_name="Спринт",
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="sprint_items",
        verbose_name="Задача",
    )
    position = models.PositiveIntegerField("Порядок", default=0)
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.PLANNED
    )
    transferred_to = models.ForeignKey(
        Sprint,
        on_delete=models.SET_NULL,
        related_name="transferred_from_items",
        null=True,
        blank=True,
        verbose_name="Перенесена в спринт",
    )
    transferred_at = models.DateTimeField("Перенесена", null=True, blank=True)
    added_at = models.DateTimeField("Добавлена", auto_now_add=True)

    class Meta:
        ordering = ("position", "added_at")
        constraints = [
            models.UniqueConstraint(
                fields=("sprint", "task"), name="unique_task_in_sprint"
            ),
        ]
        verbose_name = "Задача спринта"
        verbose_name_plural = "Задачи спринта"

    def __str__(self):
        return f"{self.sprint}: {self.task.number}"
