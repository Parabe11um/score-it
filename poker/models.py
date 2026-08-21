import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.urls import reverse


ESTIMATION_VALUES = (0, 2, 4, 8, 12, 20, 32, 52)
ESTIMATION_CHOICES = tuple((value, str(value)) for value in ESTIMATION_VALUES)


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
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.UNESTIMATED
    )
    estimate_sum = models.PositiveIntegerField("Сумма голосов", null=True, blank=True)
    estimate_count = models.PositiveIntegerField(
        "Количество голосов", null=True, blank=True
    )
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
        return self.rounds.filter(
            task=self.current_task,
            status__in=(VotingRound.Status.VOTING, VotingRound.Status.REVEALED),
        ).first()


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
    joined_at = models.DateTimeField("Подключился", auto_now_add=True)
    last_seen_at = models.DateTimeField("Последняя активность", auto_now=True)

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
    value = models.PositiveSmallIntegerField("Оценка", choices=ESTIMATION_CHOICES)
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
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sprints",
        verbose_name="Проект",
    )
    name = models.CharField("Название", max_length=160)
    goal = models.CharField("Цель спринта", max_length=500, blank=True)
    start_date = models.DateField("Дата начала", null=True, blank=True)
    end_date = models.DateField("Дата завершения", null=True, blank=True)
    capacity = models.DecimalField(
        "Плановая ёмкость", max_digits=8, decimal_places=2, null=True, blank=True
    )
    tasks = models.ManyToManyField(Task, through="SprintTask", related_name="sprints")
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Спринт"
        verbose_name_plural = "Спринты"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("poker:sprint_detail", args=[self.pk])

    @property
    def total_estimate(self):
        total = Decimal("0")
        for item in self.sprint_tasks.select_related("task"):
            if item.task.estimate is not None:
                total += item.task.estimate
        return total

    @property
    def total_estimate_display(self):
        rounded = self.total_estimate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return format(rounded.normalize(), "f")


class SprintTask(models.Model):
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
    added_at = models.DateTimeField("Добавлена", auto_now_add=True)

    class Meta:
        ordering = ("position", "added_at")
        constraints = [
            models.UniqueConstraint(
                fields=("sprint", "task"), name="unique_task_in_sprint"
            ),
            models.UniqueConstraint(
                fields=("task",), name="task_can_belong_to_only_one_sprint"
            ),
        ]
        verbose_name = "Задача спринта"
        verbose_name_plural = "Задачи спринта"

    def __str__(self):
        return f"{self.sprint}: {self.task.number}"
