from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("poker", "0009_task_competency"),
    ]

    operations = [
        migrations.AddField(
            model_name="sprint",
            name="analysis_capacity",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=8,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="Ёмкость аналитики",
            ),
        ),
        migrations.AddField(
            model_name="sprint",
            name="development_capacity",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=8,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="Ёмкость разработки",
            ),
        ),
        migrations.AddField(
            model_name="sprint",
            name="testing_capacity",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=8,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="Ёмкость тестирования",
            ),
        ),
    ]
