import re

from django import forms

from .models import Project, Sprint, Task, VotingSession


class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                continue
            current = field.widget.attrs.get("class", "")
            css_class = "form-check-input" if isinstance(
                field.widget, forms.CheckboxInput
            ) else "form-control"
            field.widget.attrs["class"] = f"{current} {css_class}".strip()


class ProjectForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Project
        fields = ("name",)
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Например, ABS Core"})}


class BulkTaskImportForm(BootstrapFormMixin, forms.Form):
    tasks_text = forms.CharField(
        label="Задачи",
        widget=forms.Textarea(
            attrs={
                "rows": 9,
                "placeholder": "ABS-123 | Добавить новый вид операции\nABS-124 | Исправить расчёт комиссии",
            }
        ),
        help_text="Одна задача на строку: номер | название. Также поддерживаются табуляция и пробел после номера.",
    )

    def clean_tasks_text(self):
        value = self.cleaned_data["tasks_text"]
        parsed = []
        errors = []

        for line_number, raw_line in enumerate(value.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue

            line = re.sub(r"^\s*\d+[.)]\s+", "", line)
            if "|" in line:
                number, title = line.split("|", 1)
            elif "\t" in line:
                number, title = line.split("\t", 1)
            else:
                parts = line.split(maxsplit=1)
                if len(parts) != 2:
                    errors.append(line_number)
                    continue
                number, title = parts

            number = number.strip()
            title = title.strip()
            if not number or not title:
                errors.append(line_number)
                continue
            parsed.append((number[:80], title[:500]))

        if errors:
            lines = ", ".join(map(str, errors))
            raise forms.ValidationError(
                f"Не удалось распознать строки: {lines}. Используйте формат «номер | название»."
            )
        if not parsed:
            raise forms.ValidationError("Добавьте хотя бы одну задачу.")

        self.parsed_tasks = parsed
        return value


class VotingSessionForm(BootstrapFormMixin, forms.ModelForm):
    task_ids = forms.ModelMultipleChoiceField(
        label="Задачи для оценки",
        queryset=Task.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text="Неоценённые задачи выбраны автоматически.",
    )

    class Meta:
        model = VotingSession
        fields = ("name", "minimum_participants")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Например, Оценка спринта 24"}),
            "minimum_participants": forms.NumberInput(
                attrs={"min": 1, "max": 100, "inputmode": "numeric"}
            ),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        if project is None:
            return
        tasks = project.tasks.filter(completed_at__isnull=True)
        self.fields["task_ids"].queryset = tasks
        if not self.is_bound:
            self.initial["task_ids"] = tasks.filter(status=Task.Status.UNESTIMATED)


class JoinRoomForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(
        label="Ваше имя",
        max_length=100,
        widget=forms.TextInput(
            attrs={"placeholder": "Как вас показать команде", "autocomplete": "name"}
        ),
    )


class SprintForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Sprint
        fields = ("name", "goal", "start_date", "end_date", "capacity")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Например, Спринт 24"}),
            "goal": forms.TextInput(attrs={"placeholder": "Необязательно"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "capacity": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "Например, 80"}
            ),
        }

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "Дата завершения не может быть раньше начала.")
        return cleaned
