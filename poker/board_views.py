import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import connection, transaction
from django.db.models import F, Q
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .board_payloads import (
    MAX_BOARD_IMAGES,
    MAX_OBJECTS,
    SHAPES,
    InvalidBoardPayload,
    arrow_ends,
    coordinate,
    normalize_image,
    read_history,
    read_mutation,
)
from .models import Board, BoardElement, BoardLink, Project


GRANT_KEY = "board_link_grants"
MAX_LINKS = 12
MAX_GRANTS = 48
CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
CLIENT_NUMBER = re.compile(r"[0-9]{1,16}\Z")
EDITOR_CSP = (
    "default-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


class BoardForm(forms.ModelForm):
    class Meta:
        model = Board
        fields = ("name",)
        labels = {"name": "Название доски"}


class BoardLinkForm(forms.Form):
    permission = forms.ChoiceField(label="Права по ссылке", choices=BoardLink.Permission.choices)
    hours = forms.TypedChoiceField(
        label="Срок действия",
        choices=((24, "24 часа"), (72, "3 дня"), (168, "7 дней")),
        coerce=int,
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def project_boards(request, pk):
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    form = BoardForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        board = form.save(commit=False)
        board.project = project
        board.save()
        return redirect(board)
    return render(request, "poker/project_boards.html", {
        "project": project, "project_section": "boards", "form": form, "boards": project.boards.all(),
    }, status=400 if request.method == "POST" else 200)


def _owner_board(request, pk, board_id):
    if not request.user.is_authenticated:
        raise Http404
    return get_object_or_404(
        Board.objects.select_related("project"),
        pk=board_id, project_id=pk, project__owner=request.user,
    )


def _active_links():
    return BoardLink.objects.filter(revoked_at__isnull=True, expires_at__gt=timezone.now())


def _grants(request):
    return request.session.get(GRANT_KEY, [])


@dataclass(frozen=True)
class BoardAccess:
    board: Board
    link: BoardLink | None = None

    @property
    def can_edit(self):
        return self.link is None or self.link.permission == BoardLink.Permission.EDIT


def _resolve(request, pk=None, board_id=None, link_id=None):
    if link_id is None:
        return BoardAccess(_owner_board(request, pk, board_id))
    if str(link_id) not in _grants(request):
        raise Http404
    link = get_object_or_404(_active_links().select_related("board"), pk=link_id)
    return BoardAccess(link.board, link)


@sensitive_variables("issued_url", "context")
def _editor(request, access, *, link_form=None, issued_url=None, status=200):
    board = access.board
    guest = access.link is not None
    base, args = ("board_guest", (access.link.pk,)) if guest else (
        "board", (board.project_id, board.pk),
    )
    context = {
        "board": board, "can_edit": access.can_edit, "guest": guest,
        "state_url": reverse("poker:" + base + "_state", args=args),
        "change_url": reverse("poker:" + base + "_change", args=args),
        "history_url": reverse("poker:" + base + "_history", args=args),
        "upload_url": reverse("poker:" + base + "_upload", args=args),
        # Add an element ID on the client below this validated same-origin path.
        "image_base": reverse("poker:" + base + "_image", args=(*args, uuid.UUID(int=0))).rsplit("/", 2)[0] + "/",
    }
    if not guest:
        context.update(
            project=board.project, project_section="boards",
            links=_active_links().filter(board=board).order_by("-created_at"),
            link_form=link_form if link_form is not None else BoardLinkForm(initial={"permission": "edit", "hours": 24}),
            issued_url=issued_url,
        )
    filename = "board_guest.html" if guest else "board_detail.html"
    response = render(request, "poker/" + filename, context, status=status)
    response["Content-Security-Policy"] = EDITOR_CSP
    return response


@login_required
@never_cache
@require_GET
def board_detail(request, pk, board_id):
    return _editor(request, _resolve(request, pk, board_id))


def _lock_board(board_id):
    # SQLite is used for local development. Its SELECT FOR UPDATE is a no-op;
    # take its writer lock before reading the board to avoid snapshot upgrades.
    if connection.vendor == "sqlite":
        Board.objects.filter(pk=board_id).update(revision=F("revision"))
    return get_object_or_404(Board.objects.select_for_update(), pk=board_id)


def _writable_board(access):
    board = _lock_board(access.board.pk)
    if access.link is not None and not _active_links().filter(
        pk=access.link.pk, board=board, permission=BoardLink.Permission.EDIT,
    ).exists():
        raise Http404
    return board


@login_required
@never_cache
@require_POST
@sensitive_variables("code", "issued_url")
def board_link_create(request, pk, board_id):
    access = _resolve(request, pk, board_id)
    form = BoardLinkForm(request.POST)
    issued_url = None
    if form.is_valid():
        with transaction.atomic():
            board = _lock_board(access.board.pk)
            if _active_links().filter(board=board).count() >= MAX_LINKS:
                form.add_error(None, "Сначала отзовите одну из действующих ссылок.")
            else:
                code = secrets.token_urlsafe(32)
                link = BoardLink.objects.create(
                    board=board, secret_digest=hashlib.sha256(code.encode("ascii")).hexdigest(),
                    permission=form.cleaned_data["permission"],
                    expires_at=timezone.now() + timedelta(hours=form.cleaned_data["hours"]),
                )
                issued_url = request.build_absolute_uri(reverse("poker:board_guest", args=(link.pk,))) + "#" + code
    if issued_url is None:
        return _editor(request, access, link_form=form, status=400)
    return _editor(request, access, issued_url=issued_url, status=201)


@login_required
@never_cache
@require_POST
def board_link_revoke(request, pk, board_id, link_id):
    board = _owner_board(request, pk, board_id)
    with transaction.atomic():
        _lock_board(board.pk)
        link = get_object_or_404(BoardLink, board=board, pk=link_id)
        if link.revoked_at is None:
            link.revoked_at = timezone.now()
            link.save(update_fields=("revoked_at",))
    return redirect(board)


@never_cache
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("code")
@sensitive_variables("code", "digest")
def board_guest(request, link_id):
    link = get_object_or_404(_active_links().select_related("board"), pk=link_id)
    grants = _grants(request)
    if request.method == "POST":
        code = request.POST.get("code", "")
        if CODE_PATTERN.fullmatch(code) is None:
            raise Http404
        digest = hashlib.sha256(code.encode("ascii")).hexdigest()
        if not constant_time_compare(digest, link.secret_digest):
            raise Http404
        value = str(link.pk)
        request.session[GRANT_KEY] = ([g for g in grants if g != value] + [value])[-MAX_GRANTS:]
        return redirect("poker:board_guest", link_id=link.pk)
    if str(link.pk) in grants:
        return _editor(request, BoardAccess(link.board, link))
    response = render(request, "poker/board_invitation.html")
    response["Content-Security-Policy"] = EDITOR_CSP
    return response


def _item(row):
    return {
        "id": str(row["key"]), "order": row["pk"], "revision": row["revision"],
        "kind": row["kind"], "data": {} if row["deleted"] else row["data"], "deleted": row["deleted"],
    }


ITEM_FIELDS = ("key", "pk", "revision", "kind", "data", "deleted")


def _one(board, key):
    row = board.elements.filter(key=key).values(*ITEM_FIELDS).first()
    return _item(row) if row is not None else None


def _snapshot(element):
    return _item({field: getattr(element, field) for field in ITEM_FIELDS})


def _stored(element, before, related=()):
    after = _snapshot(element)
    return JsonResponse({"element": after, "changes": [{"before": before, "after": after}, *related]})


def _attached_arrows(board, keys):
    return board.elements.filter(kind="arrow", deleted=False).filter(
        Q(data__source__in=keys) | Q(data__target__in=keys),
    )


def _live_shapes(board, keys):
    return {str(key) for key in board.elements.filter(
        key__in=keys, deleted=False, kind__in=SHAPES,
    ).values_list("key", flat=True)}


def _mark_deleted(element):
    element.deleted = True
    # The browser holds the picture for undo. Keep only its digest in the
    # tombstone so a replay can restore the exact sanitized bytes.
    picture = element.image if element.kind == "image" else None
    element.data = {"sha256": hashlib.sha256(picture).hexdigest()} if picture else {}
    element.image = None


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


@never_cache
@require_GET
def board_state(request, **scope):
    access = _resolve(request, **scope)
    after = request.GET.get("after")
    if after is not None and (not CLIENT_NUMBER.fullmatch(after) or int(after) > access.board.revision):
        return _error("Некорректная версия доски.")
    rows = access.board.elements.filter(revision__lte=access.board.revision)
    if after is None:
        rows = rows.filter(deleted=False)
    else:
        rows = rows.filter(revision__gt=int(after))
    return JsonResponse({
        "revision": access.board.revision,
        "elements": [_item(row) for row in rows.order_by("pk").values(*ITEM_FIELDS)],
    })


@never_cache
@require_POST
def board_change(request, **scope):
    access = _resolve(request, **scope)
    if not access.can_edit:
        return HttpResponseForbidden()
    try:
        key, version, kind, data = read_mutation(request)
    except InvalidBoardPayload as error:
        return _error(str(error))
    with transaction.atomic():
        board = _writable_board(access)
        element = board.elements.defer("image").filter(key=key).first()
        before = _snapshot(element) if element is not None else None
        if element is None:
            if version or kind is None or kind == "image":
                return _error("Элемент не найден.", 409)
            if board.elements.count() >= MAX_OBJECTS:
                return _error("Достигнут лимит элементов доски.", 409)
            element = BoardElement(board=board, key=key, kind=kind)
        elif element.deleted or element.revision != version or (kind is not None and kind != element.kind):
            return JsonResponse({
                "error": "Элемент обновился. Проверьте его и повторите правку.",
                "element": before,
            }, status=409)

        # The board lock keeps the endpoints valid until the arrow is saved.
        if kind == "arrow" and (ends := arrow_ends(data)) != _live_shapes(board, ends):
            return _error("Блок недоступен. Выберите фигуру на этой доске или оставьте конец свободным.", 409)

        board.revision += 1
        element.revision = board.revision
        related = []
        if kind is None:
            _mark_deleted(element)
            if element.kind in SHAPES:
                arrows = _attached_arrows(board, [str(key)])
                for row in arrows.values(*ITEM_FIELDS):
                    previous = _item(row)
                    related.append({"before": previous, "after": {**previous,
                        "deleted": True, "data": {}, "revision": board.revision}})
                arrows.update(deleted=True, data={}, revision=board.revision)
        else:
            element.data = data
        board.save(update_fields=("revision",))
        element.save()
    return _stored(element, before, related)


@never_cache
@require_POST
def board_history(request, **scope):
    """Replay one action atomically, with an exact revision for every row."""
    access = _resolve(request, **scope)
    if not access.can_edit:
        return HttpResponseForbidden()
    try:
        mutations, upload = read_history(request)
    except InvalidBoardPayload as error:
        return _error(str(error))
    with transaction.atomic():
        board = _writable_board(access)
        keys = [key for key, _, _, _ in mutations]
        elements = {row.key: row for row in board.elements.filter(key__in=keys).defer("image")}
        for key, revision, kind, _ in mutations:
            element = elements.get(key)
            if element is None or element.revision != revision or (
                element.deleted if kind is None else element.kind != kind
            ):
                return _error("Правка пропущена: элемент уже изменился.", 409)

        retiring = {str(key) for key, _, kind, _ in mutations if kind is None and elements[key].kind in SHAPES}
        if retiring and _attached_arrows(board, retiring).exclude(key__in=keys).exists():
            return _error("Правка пропущена: у фигуры появились другие стрелки.", 409)
        restored = {str(key) for key, _, kind, _ in mutations if kind in SHAPES}
        endpoints = set().union(*(arrow_ends(data) for _, _, kind, data in mutations if kind == "arrow"))
        allowed = (_live_shapes(board, endpoints) | restored) - retiring
        if not endpoints <= allowed:
            return _error("Правка пропущена: фигура для привязки недоступна.", 409)

        pictures = [elements[key] for key, _, kind, _ in mutations if kind == "image" and elements[key].deleted]
        removed_pictures = [elements[key] for key, _, kind, _ in mutations
                            if kind is None and elements[key].kind == "image"]
        if len(pictures) != int(upload is not None):
            return _error("Не удалось вернуть изображение.")
        if pictures:
            picture = pictures[0]
            binary = upload.read()
            if not constant_time_compare(hashlib.sha256(binary).hexdigest(), picture.data.get("sha256", "")):
                return _error("Не удалось вернуть изображение.")
            active_images = board.elements.filter(kind="image", deleted=False).count() - len(removed_pictures)
            if active_images >= MAX_BOARD_IMAGES:
                return _error("Достигнут лимит изображений доски.", 409)
            picture.image = binary

        board.revision += 1
        for key, _, kind, data in mutations:
            element = elements[key]
            element.revision = board.revision
            if kind is None:
                _mark_deleted(element)
            else:
                element.deleted = False
                element.data = data
        board.elements.bulk_update(elements.values(), ("revision", "deleted", "data"))
        if pictures or removed_pictures:
            board.elements.bulk_update(pictures + removed_pictures, ("image",))
        board.save(update_fields=("revision",))
        return JsonResponse({"elements": [_snapshot(elements[key]) for key in keys]})


@never_cache
@require_POST
def board_upload(request, **scope):
    access = _resolve(request, **scope)
    if not access.can_edit:
        return HttpResponseForbidden()
    try:
        key = uuid.UUID(request.POST.get("id", ""))
        x, y = (coordinate(float(request.POST.get(axis, ""))) for axis in ("x", "y"))
    except ValueError:
        return _error("Некорректные данные изображения.")
    try:
        binary, content_type, (width, height) = normalize_image(request.FILES.get("image"))
    except InvalidBoardPayload as error:
        return _error(str(error))
    with transaction.atomic():
        board = _writable_board(access)
        existing = _one(board, key)
        if existing is not None:
            if existing["kind"] == "image" and not existing["deleted"]:
                return JsonResponse({"element": existing})
            return _error("Элемент уже изменился.", 409)
        if board.elements.count() >= MAX_OBJECTS or board.elements.filter(
            kind="image", deleted=False,
        ).count() >= MAX_BOARD_IMAGES:
            return _error("Достигнут лимит элементов или изображений доски.", 409)
        scale = min(320 / max(width, height), 1)
        board.revision += 1
        element = board.elements.create(
            key=key, kind="image", revision=board.revision,
            data={"x": x, "y": y, "w": max(16, round(width * scale, 1)), "h": max(16, round(height * scale, 1))},
            image=binary, image_type=content_type,
        )
        board.save(update_fields=("revision",))
    return _stored(element, None)


@never_cache
@require_GET
def board_image(request, element_id, **scope):
    access = _resolve(request, **scope)
    element = get_object_or_404(access.board.elements, key=element_id, kind="image", deleted=False)
    if element.image_type not in ("image/png", "image/jpeg") or not element.image:
        raise Http404
    response = HttpResponse(bytes(element.image), content_type=element.image_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    return response
