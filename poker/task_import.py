"""Bounded CSV/XLSX readers and shared EVA column/type mapping."""

import csv
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO, StringIO
from itertools import chain
from pathlib import Path
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from django.core.exceptions import ValidationError
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .models import Task


EVA_TASK_URL_PREFIX = "https://projects.cifra.works/project/Kanban/KBB-000451?popup="
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 512
HOUR_ESTIMATE_HEADER = "Оценка задачи, час"

# Deliberately exclude EVA's unrelated fields «Оценка» and «Story Point».
HEADERS = {
    "quota": ("Тип квоты",),
    "number": ("Код", "Код задачи", "Номер задачи", "Номер"),
    "title": ("Наименование", "Название задачи", "Название"),
    "competency": ("Логический тип.Имя логического типа", "Логический тип", "Тип задачи"),
    "identifier": ("Идентификатор объекта",),
    "estimate": (HOUR_ESTIMATE_HEADER, "Оценка задачи, ч", "Оценка задачи, часы"),
    "html_description": ("Текст", "Описание"),
    "plain_description": ("Текст без html", "Описание без html"),
    # EVA also exports an unrelated bare «Статус» column. Never use it here.
    "eva_status": ("Статус.Имя статуса", "Статус задачи"),
    "eva_status_type": ("Кеш: Тип статуса", "Кэш: Тип статуса", "Тип статуса"),
    "eva_sprints": ("Спринты", "Спринт", "Спринт.Название", "Спринт.Наименование"),
}
COMPETENCIES = {
    "системный анализ": Task.Competency.ANALYSIS,
    "бизнес-анализ": Task.Competency.ANALYSIS,
    "бизнес анализ": Task.Competency.ANALYSIS,
    "аналитика": Task.Competency.ANALYSIS,
    "анализ": Task.Competency.ANALYSIS,
    "analysis": Task.Competency.ANALYSIS,
    "разработка": Task.Competency.DEVELOPMENT,
    "разработка абс": Task.Competency.DEVELOPMENT_ABS,
    "разработка битрикс": Task.Competency.DEVELOPMENT,
    "разработка be": Task.Competency.DEVELOPMENT_BE,
    "разработка fe": Task.Competency.DEVELOPMENT_FE,
    "development": Task.Competency.DEVELOPMENT,
    "dev": Task.Competency.DEVELOPMENT,
    "тестирование": Task.Competency.TESTING,
    "тестирование - на dev": Task.Competency.TESTING,
    "тестирование - на test": Task.Competency.TESTING,
    "testing": Task.Competency.TESTING,
    "qa": Task.Competency.TESTING,
    "дефект": Task.Competency.DEFECT,
    "defect": Task.Competency.DEFECT,
    "bug": Task.Competency.DEFECT,
    "": Task.Competency.NONE,
    "без типа": Task.Competency.NONE,
}


def _text(value):
    return "" if value is None else str(value).strip()


def _normalise(value):
    return " ".join(_text(value).lstrip("\ufeff").split()).casefold()


class _DescriptionText(HTMLParser):
    """Keep paragraphs and lists; never store executable source markup."""

    blocks = {
        "p", "div", "br", "li", "ul", "ol", "tr",
        "h1", "h2", "h3", "h4", "blockquote",
    }
    ignored = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignore_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored:
            self.ignore_depth += 1
        if not self.ignore_depth and tag in self.blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.ignored:
            self.ignore_depth = max(0, self.ignore_depth - 1)
        elif not self.ignore_depth and tag in self.blocks:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignore_depth:
            self.parts.append(data)


def description_text(value):
    parser = _DescriptionText()
    parser.feed(_text(value))
    parser.close()
    lines = [line.strip() for line in "".join(parser.parts).replace("\xa0", " ").splitlines()]
    return "\n".join(line for line in lines if line)


@dataclass(frozen=True)
class ImportedTask:
    number: str
    title: str
    competency: str
    description: str
    external_url: str
    quota: str = ""


def _column_mapping(headers):
    mapping = {}
    normalised = [_normalise(value) for value in headers]
    for key, aliases in HEADERS.items():
        for alias in aliases:
            indexes = [
                i for i, value in enumerate(normalised)
                if value == _normalise(alias)
            ]
            if len(indexes) > 1:
                raise ValidationError(f"Колонка «{alias}» встречается несколько раз.")
            if indexes:
                mapping[key] = indexes[0]
                break
    required = ("number", "title", "competency", "identifier", "estimate")
    missing = [HEADERS[key][0] for key in required if key not in mapping]
    if not {"html_description", "plain_description"} & mapping.keys():
        missing.append("Текст или Текст без html")
    if missing:
        raise ValidationError(
            "Не найдены колонки: " + ", ".join(missing)
            + ". Используйте выгрузку EVA «все поля»."
        )
    return mapping


def _csv_rows(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1251")
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    delimiter = None
    if lines and re.fullmatch(r"sep=[;,\t]", lines[0], re.IGNORECASE):
        delimiter = lines[0][-1]
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if delimiter is None:
        # A comma inside «Оценка задачи, час» is not a CSV delimiter.
        # Match known headings rather than relying on Sniffer's comma preference.
        header = next((line for line in text.splitlines() if line.strip()), "")
        known_headers = {
            _normalise(alias) for aliases in HEADERS.values() for alias in aliases
        }
        candidates = []
        for candidate in (",", ";", "\t"):
            fields = next(csv.reader([header], delimiter=candidate))
            matches = sum(_normalise(value) in known_headers for value in fields)
            candidates.append((matches, len(fields), candidate))
        matches, field_count, delimiter = max(candidates)
        if not matches and field_count < 2:
            raise ValidationError(
                "Не удалось определить разделитель CSV: используйте запятую, "
                "точку с запятой или табуляцию."
            )
    reader = csv.reader(StringIO(text, newline=""), delimiter=delimiter, strict=True)
    return _bounded_rows(reader)


def _bounded_rows(rows):
    result = []
    for row in rows:
        if len(row) > MAX_COLUMNS:
            raise ValidationError(f"В файле больше {MAX_COLUMNS} колонок.")
        result.append(tuple(row))
        if len(result) > MAX_ROWS + 1:
            raise ValidationError(f"Загрузите не более {MAX_ROWS} строк за один раз.")
    return result


def _xlsx_rows(data):
    with ZipFile(BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
            raise ValidationError(
                "Содержимое XLSX слишком велико. Разделите выгрузку на несколько файлов."
            )
    # Keep formulas visible: an estimate formula without a cached value is filled.
    workbook = load_workbook(
        BytesIO(data), read_only=True, data_only=False, keep_links=False,
    )
    try:
        candidates = []
        errors = []
        for sheet in workbook:
            if sheet.max_column and sheet.max_column > MAX_COLUMNS:
                continue
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, ())
            if not any(_text(value) for value in header):
                continue
            try:
                _column_mapping(header)
            except ValidationError as exc:
                errors.append(exc)
                continue
            candidates.append(_bounded_rows(chain((header,), rows)))
        if not candidates:
            if errors:
                raise errors[0]
            raise ValidationError("В XLSX не найден лист с заголовками задач EVA.")
        if len(candidates) > 1:
            raise ValidationError("В XLSX несколько листов с задачами. Оставьте один лист для импорта.")
        return candidates[0]
    finally:
        workbook.close()


def read_task_file_rows(upload):
    extension = Path(upload.name).suffix.lower()
    if extension not in (".csv", ".xlsx"):
        raise ValidationError("Выберите файл CSV или XLSX.")
    if upload.size > MAX_FILE_BYTES:
        raise ValidationError("Размер файла не должен превышать 10 МБ.")
    data = upload.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError("Размер файла не должен превышать 10 МБ.")
    try:
        rows = _xlsx_rows(data) if extension == ".xlsx" else _csv_rows(data)
    except (
        BadZipFile, InvalidFileException, KeyError, ValueError,
        OSError, csv.Error, SyntaxError,
    ) as exc:
        raise ValidationError(
            "Не удалось прочитать файл. Повторите выгрузку в CSV или XLSX."
        ) from exc
    if not rows:
        raise ValidationError("Файл пуст.")
    return rows


def task_from_row(row, columns, row_number):
    def value(key):
        index = columns.get(key)
        return _text(row[index]) if index is not None and index < len(row) else ""

    number, title = value("number"), value("title")
    type_name, identifier = value("competency"), value("identifier")
    error = None
    if len(row) <= max(columns.values()):
        error = "недостаточно колонок; проверьте разделитель CSV"
    elif not number or len(number) > 80:
        error = "нужен код задачи длиной до 80 символов"
    elif not title or len(title) > 500:
        error = "нужно название задачи длиной до 500 символов"
    elif _normalise(type_name) not in COMPETENCIES:
        error = f"неизвестный тип «{type_name[:80]}»; укажите аналитику, разработку АБС/BE/FE, тестирование или дефект"
    elif not re.fullmatch(r"CmfTask:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identifier):
        error = "нужен идентификатор объекта CmfTask:UUID для ссылки в EVA"
    if len(value("quota")) > 200:
        error = "тип квоты превышает 200 символов"
    if error:
        raise ValidationError(f"Строка {row_number}: {error}.")
    html = value("html_description")
    description = description_text(html) if html else value("plain_description")
    if len(description) > 100000:
        raise ValidationError(f"Строка {row_number}: описание превышает 100 000 символов.")
    return ImportedTask(
        number=number, title=title, competency=COMPETENCIES[_normalise(type_name)],
        description=description, quota=value("quota"),
        external_url=f"{EVA_TASK_URL_PREFIX}CmfTask:{UUID(identifier.split(':', 1)[1])}",
    )


def parse_task_file(upload):
    from .sprint_import import parse_sprint_file
    return parse_sprint_file(upload, require_status=False)


def save_task_import(project, parsed):
    from .sprint_import import save_sprint_import
    return save_sprint_import(project, parsed)
