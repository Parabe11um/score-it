from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("poker", "0010_sprint_competency_capacities"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sprint",
            name="capacity",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=8,
                null=True,
                verbose_name="Плановая ёмкость, часы",
            ),
        ),
        migrations.AlterField(
            model_name="sprint",
            name="analysis_capacity",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=8,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="Ёмкость аналитики, часы",
            ),
        ),
        migrations.AlterField(
            model_name="sprint",
            name="development_capacity",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=8,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="Ёмкость разработки, часы",
            ),
        ),
        migrations.AlterField(
            model_name="sprint",
            name="testing_capacity",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=8,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="Ёмкость тестирования, часы",
            ),
        ),
        migrations.AlterField(
            model_name="task",
            name="estimate_sum",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="Сумма оценок, часы",
            ),
        ),
        migrations.AlterField(
            model_name="vote",
            name="value",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, "0"),
                    (1, "1"),
                    (2, "2"),
                    (4, "4"),
                    (8, "8"),
                    (12, "12"),
                    (20, "20"),
                    (32, "32"),
                    (52, "52"),
                ],
                verbose_name="Оценка, часы",
            ),
        ),
    ]
