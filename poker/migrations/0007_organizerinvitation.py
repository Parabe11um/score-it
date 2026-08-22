import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import poker.models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("poker", "0006_sprint_task_transfer_history"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizerInvitation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "recipient_label",
                    models.CharField(
                        blank=True,
                        help_text="Необязательная пометка, видимая только в админке.",
                        max_length=160,
                        verbose_name="Для кого",
                    ),
                ),
                (
                    "token",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                        verbose_name="Токен",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                (
                    "expires_at",
                    models.DateTimeField(
                        default=poker.models.default_organizer_invitation_expiry,
                        editable=False,
                        verbose_name="Действует до",
                    ),
                ),
                ("used_at", models.DateTimeField(blank=True, null=True, verbose_name="Использовано")),
                (
                    "created_by",
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_organizer_invitations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Создал",
                    ),
                ),
                (
                    "used_by",
                    models.OneToOneField(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="organizer_invitation",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Созданный организатор",
                    ),
                ),
            ],
            options={
                "verbose_name": "Приглашение организатора",
                "verbose_name_plural": "Приглашения организаторов",
                "ordering": ("-created_at",),
            },
        ),
    ]
