import re

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import Project, ProjectMember, Sprint, SprintResource, Task, VotingSession
from .task_import import parse_task_file


class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(
                field.widget,
                (forms.CheckboxSelectMultiple, forms.RadioSelect),
            ):
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


class OrganizerRegistrationForm(BootstrapFormMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        fields = ("username", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Логин"
        self.fields["username"].widget.attrs.update(
            {
                "autocomplete": "username",
                "placeholder": "Придумайте логин",
            }
        )
        self.fields["password1"].label = "Пароль"
        self.fields["password1"].widget.attrs["autocomplete"] = "new-password"
        self.fields["password2"].label = "Повторите пароль"
        self.fields["password2"].widget.attrs["autocomplete"] = "new-password"

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True
        if commit:
            user.save()
        return user


class TaskMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, task):
        competency = (
            f"{task.get_competency_display()} · "
            if task.competency
            else ""
        )
        return f"{competency}{task.number} — {task.title}"


class BulkTaskImportForm(BootstrapFormMixin, forms.Form):
    competency = forms.ChoiceField(
        label="Тип задач в этом списке",
        choices=Task.Competency.choices,
        required=False,
        initial=Task.Competency.NONE,
        widget=forms.RadioSelect,
        help_text="Выбранный тип применяется ко всему списку. Его можно переопределить в отдельной строке.",
    )
    tasks_text = forms.CharField(
        label="Задачи",
        widget=forms.Textarea(
            attrs={
                "rows": 9,
                "placeholder": "ABS-123 | Добавить новый вид операции\nABS-124 | Исправить расчёт комиссии",
            }
        ),
        help_text=(
            "Одна задача на строку: номер | название. Для смешанного списка можно начать строку с "
            "[Аналитика], [Разработка] или [Тестирование]."
        ),
    )

    competency_aliases = {
        "аналитика": Task.Competency.ANALYSIS,
        "анализ": Task.Competency.ANALYSIS,
        "analysis": Task.Competency.ANALYSIS,
        "analytics": Task.Competency.ANALYSIS,
        "разработка": Task.Competency.DEVELOPMENT,
        "dev": Task.Competency.DEVELOPMENT,
        "development": Task.Competency.DEVELOPMENT,
        "тестирование": Task.Competency.TESTING,
        "тест": Task.Competency.TESTING,
        "qa": Task.Competency.TESTING,
        "testing": Task.Competency.TESTING,
        "без типа": Task.Competency.NONE,
        "none": Task.Competency.NONE,
    }

    def clean_tasks_text(self):
        value = self.cleaned_data["tasks_text"]
        default_competency = self.cleaned_data.get(
            "competency", Task.Competency.NONE
        )
        parsed = []
        errors = []

        for line_number, raw_line in enumerate(value.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue

            line = re.sub(r"^\s*\d+[.)]\s+", "", line)
            competency = default_competency
            competency_match = re.match(r"^\[([^\]]+)\]\s*", line)
            if competency_match:
                competency_name = competency_match.group(1).strip().lower()
                if competency_name not in self.competency_aliases:
                    errors.append(line_number)
                    continue
                competency = self.competency_aliases[competency_name]
                line = line[competency_match.end():].strip()

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
            parsed.append((number[:80], title[:500], competency))

        if errors:
            lines = ", ".join(map(str, errors))
            raise forms.ValidationError(
                f"Не удалось распознать строки: {lines}. Используйте формат «номер | название»."
            )
        if not parsed:
            raise forms.ValidationError("Добавьте хотя бы одну задачу.")

        self.parsed_tasks = parsed
        return value


class TaskFileImportForm(BootstrapFormMixin, forms.Form):
    task_file = forms.FileField(
        label="Файл выгрузки EVA",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx"}),
        help_text="CSV или XLSX, до 10 МБ и 5000 строк. В EVA выберите экспорт «все поля».",
    )

    def clean_task_file(self):
        upload = self.cleaned_data["task_file"]
        self.parsed_import = parse_task_file(upload)
        return upload


class SprintFileImportForm(TaskFileImportForm):
    def clean_task_file(self):
        from .sprint_import import parse_sprint_file

        upload = self.cleaned_data["task_file"]
        self.parsed_import = parse_sprint_file(upload)
        return upload


class VotingSessionForm(BootstrapFormMixin, forms.ModelForm):
    task_ids = TaskMultipleChoiceField(
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
    capacity_source = forms.ChoiceField(
        label="Расчёт ёмкости", choices=Sprint.CapacitySource.choices, required=False,
        help_text="Для расчёта по сотрудникам заполните команду проекта и обе даты. Календарь и резерв можно уточнить внутри спринта.",
    )

    class Meta:
        model = Sprint
        fields = (
            "name",
            "goal",
            "start_date",
            "end_date",
            "capacity_source",
            "analysis_capacity",
            "development_capacity",
            "testing_capacity",
        )
        labels = {
            "analysis_capacity": "Аналитика, часы",
            "development_capacity": "Разработка, часы",
            "testing_capacity": "Тестирование, часы",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Например, Спринт 24"}),
            "goal": forms.TextInput(attrs={"placeholder": "Необязательно"}),
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "analysis_capacity": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "Например, 24 ч"}
            ),
            "development_capacity": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "Например, 60 ч"}
            ),
            "testing_capacity": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "Например, 32 ч"}
            ),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        if project is not None and not self.is_bound:
            self.initial["capacity_source"] = (
                Sprint.CapacitySource.TEAM if project.members.filter(is_active=True).exists()
                else Sprint.CapacitySource.MANUAL
            )

    def clean_capacity_source(self):
        return self.cleaned_data.get("capacity_source") or Sprint.CapacitySource.MANUAL

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "Дата завершения не может быть раньше начала.")
        if cleaned.get("capacity_source") == Sprint.CapacitySource.TEAM:
            for field in ("start_date", "end_date"):
                if not cleaned.get(field):
                    self.add_error(field, "Укажите дату для расчёта ёмкости команды.")
            if self.project is None or not self.project.members.filter(is_active=True).exists():
                self.add_error("capacity_source", "Сначала добавьте сотрудников в команду проекта.")
        return cleaned


class SprintCapacityForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Sprint
        fields = (
            "analysis_capacity",
            "development_capacity",
            "testing_capacity",
        )
        labels = {
            "analysis_capacity": "Аналитика, часы",
            "development_capacity": "Разработка, часы",
            "testing_capacity": "Тестирование, часы",
        }
        widgets = {
            "analysis_capacity": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "Не задана"}
            ),
            "development_capacity": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "Не задана"}
            ),
            "testing_capacity": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "Не задана"}
            ),
        }


class ProjectMemberForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectMember
        fields = ("full_name", "competency", "allocation_percent", "hours_per_day", "is_active")
        widgets = {
            "allocation_percent": forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.01"}),
            "hours_per_day": forms.NumberInput(attrs={"min": "0.01", "max": 24, "step": "0.01"}),
        }

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())


class SprintSettingsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Sprint
        fields = ("capacity_source", "start_date", "end_date", "working_days_override", "reserve_percent")
        widgets = {
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "working_days_override": forms.NumberInput(attrs={"min": 0, "step": 1}),
            "reserve_percent": forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.01"}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        override = cleaned.get("working_days_override")
        if cleaned.get("capacity_source") == Sprint.CapacitySource.TEAM:
            for field in ("start_date", "end_date"):
                if not cleaned.get(field):
                    self.add_error(field, "Укажите дату для расчёта ёмкости команды.")
            if not self.instance.resources.exists():
                self.add_error("capacity_source", "Добавьте хотя бы одного сотрудника в спринт.")
        if start and end:
            if end < start:
                self.add_error("end_date", "Дата завершения не может быть раньше начала.")
            elif override is not None and override > (end - start).days + 1:
                self.add_error("working_days_override", "Рабочих дней не может быть больше календарных.")
            else:
                days = Sprint(start_date=start, end_date=end, working_days_override=override).working_days
                if self.instance.resources.filter(absence_days__gt=days).exists():
                    self.add_error("end_date", "В новом периоде отсутствие сотрудника превышает число рабочих дней. Сначала уменьшите дни отсутствия.")
        return cleaned


class SprintResourceForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SprintResource
        fields = ("competency", "allocation_percent", "hours_per_day", "absence_days")
        widgets = {
            "allocation_percent": forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.01"}),
            "hours_per_day": forms.NumberInput(attrs={"min": "0.01", "max": 24, "step": "0.01"}),
            "absence_days": forms.NumberInput(attrs={"min": 0, "step": "0.5"}),
        }
