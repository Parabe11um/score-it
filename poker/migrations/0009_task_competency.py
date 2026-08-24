from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("poker", "0008_reset_incomplete_participants"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="competency",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Без типа"),
                    ("analysis", "Аналитика"),
                    ("development", "Разработка"),
                    ("testing", "Тестирование"),
                ],
                default="",
                max_length=20,
                verbose_name="Тип задачи",
            ),
        ),
    ]
