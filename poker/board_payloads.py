"""Validate the small wire format shared by the organizer and guest editor."""

import io
import json
import math
import uuid

from PIL import Image, ImageOps


MAX_OBJECTS = 500  # Includes deletion records, so a deleted key stays deleted.
MAX_POINTS = 256
MAX_COORDINATE = 100_000
MAX_DIMENSION = 4_000
MAX_TEXT = 240
MAX_MUTATION_BYTES = 24 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_BOARD_IMAGES = 10
MAX_IMAGE_EDGE = 2048
PALETTE = ("ink", "mint", "blue", "yellow", "pink")
SHAPES = ("circle", "square", "rectangle", "note")


class InvalidBoardPayload(ValueError):
    pass


def number(value, low, high):
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise InvalidBoardPayload("Недопустимые координаты или размеры.")
    return round(value, 1)


def coordinate(value):
    return number(value, -MAX_COORDINATE, MAX_COORDINATE)


def element_data(kind, data):
    if not isinstance(data, dict) or kind not in ("stroke", *SHAPES, "image"):
        raise InvalidBoardPayload("Неизвестный элемент доски.")

    fields = {"x", "y"}
    if kind == "stroke":
        fields |= {"points", "color"}
    elif kind == "image":
        fields |= {"w", "h"}
    else:
        fields |= {"w", "h", "text", "color"}
    if data.keys() != fields:
        raise InvalidBoardPayload("Некорректные поля элемента.")

    result = {"x": coordinate(data["x"]), "y": coordinate(data["y"])}
    if kind != "image":
        if data["color"] not in PALETTE:
            raise InvalidBoardPayload("Выберите цвет из палитры.")
        result["color"] = data["color"]
    if kind == "stroke":
        points = data["points"]
        if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
            raise InvalidBoardPayload("Штрих слишком длинный. Нарисуйте его по частям.")
        if any(not isinstance(point, list) or len(point) != 2 for point in points):
            raise InvalidBoardPayload("Некорректный штрих.")
        result["points"] = [[coordinate(x), coordinate(y)] for x, y in points]
    else:
        result["w"] = number(data["w"], 16, MAX_DIMENSION)
        result["h"] = number(data["h"], 16, MAX_DIMENSION)
        if kind in ("circle", "square") and result["w"] != result["h"]:
            raise InvalidBoardPayload("У круга и квадрата стороны должны быть равны.")
        if kind != "image":
            value = data["text"]
            if (
                not isinstance(value, str)
                or len(value) > MAX_TEXT
                or value.count("\n") > 5
                or any((ord(ch) < 32 and ch != "\n") or 0xD800 <= ord(ch) <= 0xDFFF for ch in value)
            ):
                raise InvalidBoardPayload("Подпись: до 240 символов и 6 строк.")
            result["text"] = value
    return result


def read_mutation(request):
    if request.content_type != "application/json":
        raise InvalidBoardPayload("Ожидается изменение в формате JSON.")
    raw = request.read(MAX_MUTATION_BYTES + 1)
    if len(raw) > MAX_MUTATION_BYTES:
        raise InvalidBoardPayload("Изменение слишком большое.")
    try:
        change = json.loads(raw)
        if not isinstance(change, dict):
            raise ValueError
        key = uuid.UUID(change["id"])
    except (ValueError, KeyError, AttributeError, TypeError, RecursionError) as exc:
        raise InvalidBoardPayload("Изменение не удалось прочитать.") from exc
    revision = change.get("revision")
    if type(revision) is not int or not 0 <= revision < 2**53:
        raise InvalidBoardPayload("Некорректная версия элемента.")
    if change.keys() == {"id", "revision", "delete"} and change["delete"] is True:
        return key, revision, None, None
    if change.keys() != {"id", "revision", "kind", "data"}:
        raise InvalidBoardPayload("Некорректное изменение.")
    kind = change["kind"]
    return key, revision, kind, element_data(kind, change["data"])


def normalize_image(upload):
    if not upload or not 0 < upload.size <= MAX_IMAGE_BYTES:
        raise InvalidBoardPayload("Выберите PNG или JPG до 2 МБ.")
    try:
        with Image.open(upload, formats=("PNG", "JPEG")) as source:
            if max(source.size) > MAX_IMAGE_EDGE or getattr(source, "is_animated", False):
                raise InvalidBoardPayload("Нужна статичная картинка не больше 2048 × 2048.")
            as_png = source.format == "PNG"
            oriented = ImageOps.exif_transpose(source)
            mode = "RGBA" if as_png else "RGB"
            # Rebuild pixels in a new image so user-supplied metadata is not served.
            clean = Image.new(mode, oriented.size)
            clean.paste(oriented.convert(mode))
            output = io.BytesIO()
            if as_png:
                clean.save(output, "PNG", compress_level=3)
            else:
                clean.save(output, "JPEG", quality=85)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        if isinstance(exc, InvalidBoardPayload):
            raise
        raise InvalidBoardPayload("Не удалось прочитать изображение PNG или JPG.") from exc
    if output.tell() > MAX_IMAGE_BYTES:
        raise InvalidBoardPayload("После обработки изображение превышает 2 МБ.")
    return output.getvalue(), ("image/png" if as_png else "image/jpeg"), clean.size
