import hashlib
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image, PngImagePlugin

from poker.models import Board, BoardElement, BoardLink, Project


HOST = "https://testserver"


def note(text="Заметка", *, key=None, revision=0, **offsets):
    return {
        "id": str(key or uuid.uuid4()), "revision": revision, "kind": "note",
        "data": {"x": 20, "y": 25, "w": 170, "h": 108, "text": text, "color": "yellow", **offsets},
    }


def arrow(source, target):
    return {"id": str(uuid.uuid4()), "revision": 0, "kind": "arrow",
            "data": {"source": source, "target": target, "color": "ink"}}


class BoardClient(Client):
    def __init__(self):
        super().__init__(enforce_csrf_checks=True)

    def get(self, path, data=None):
        return super().get(path, data, secure=True)

    def send(self, path, data, *, as_json=False, csrf=True, origin=HOST):
        kwargs = {"secure": True, "HTTP_ORIGIN": origin}
        if csrf:
            kwargs["HTTP_X_CSRFTOKEN"] = self.cookies["csrftoken"].value
        if as_json:
            kwargs["content_type"] = "application/json"
        return super().post(path, data, **kwargs)


@override_settings(ALLOWED_HOSTS=["testserver"])
class BoardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("board-organizer")
        cls.project = Project.objects.create(owner=cls.owner, name="Внутренний проект")
        cls.board = Board.objects.create(project=cls.project, name="Совместная доска")
        cls.sibling = Board.objects.create(project=cls.project, name="Другая доска")

    def setUp(self):
        self.owner_client = BoardClient()
        self.owner_client.force_login(self.owner)
        self.path = self.board.get_absolute_url()
        self.owner_client.get(self.path)

    def make_link(self, permission="edit", board=None):
        board = board or self.board
        response = self.owner_client.send(
            reverse("poker:board_link_create", args=(board.project_id, board.pk)),
            {"permission": permission, "hours": 24},
        )
        self.assertEqual(response.status_code, 201)
        issued = urlsplit(response.context["issued_url"])
        self.assertEqual((issued.scheme, issued.netloc, issued.query), ("https", "testserver", ""))
        self.assertRegex(issued.fragment, r"^[A-Za-z0-9_-]{43}$")
        return issued.path, issued.fragment

    def open_guest(self, url, code, guest=None):
        guest = guest or BoardClient()
        landing = guest.get(url)
        self.assertEqual(landing.status_code, 200)
        attached = guest.send(url, {"code": code})
        self.assertRedirects(attached, url, fetch_redirect_response=False)
        return guest

    def make_image(self, mode="RGB", format="PNG"):
        output = io.BytesIO()
        extra = {}
        if format == "PNG":
            info = PngImagePlugin.PngInfo()
            info.add_text("Comment", "user-supplied metadata")
            extra["pnginfo"] = info
        Image.new(mode, (45, 32), "red").save(output, format, **extra)
        return SimpleUploadedFile("synthetic.png" if format == "PNG" else "synthetic.jpg",
                                  output.getvalue(), f"image/{format.lower()}")

    def test_owner_creates_and_lists_boards_within_project(self):
        other = Project.objects.create(owner=self.owner, name="Другой проект")
        Board.objects.create(project=other, name="Доска другого проекта")
        url = reverse("poker:project_boards", args=(self.project.pk,))
        response = self.owner_client.get(url)
        self.assertContains(response, self.board.name)
        self.assertNotContains(response, "Доска другого проекта")
        created = self.owner_client.send(url, {"name": "  Схема интеграции  "})
        board = self.project.boards.get(name="Схема интеграции")
        self.assertRedirects(created, board.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(self.owner_client.get(reverse("poker:board_detail", args=(other.pk, board.pk))).status_code, 404)

    def test_selector_alone_does_not_open_board_and_secret_is_not_stored_or_shown_again(self):
        url, code = self.make_link()
        link = self.board.links.get()
        self.assertEqual(link.secret_digest, hashlib.sha256(code.encode("ascii")).hexdigest())
        self.assertNotContains(self.owner_client.get(self.path), code)
        guest = BoardClient()
        landing = guest.get(url)
        self.assertNotContains(landing, self.board.name)
        self.assertNotContains(landing, self.project.name)
        self.assertNotContains(landing, code)
        self.assertIn("no-store", landing["Cache-Control"])
        self.assertEqual(landing["Referrer-Policy"], "strict-origin")
        self.assertIn("frame-ancestors 'none'", landing["Content-Security-Policy"])
        self.assertEqual(guest.get(url + "state/").status_code, 404)
        self.assertEqual(guest.send(url, {"code": "x" * 43}).status_code, 404)
        self.assertEqual(guest.send(url, {"code": code}, csrf=False).status_code, 403)
        self.assertEqual(guest.send(url, {"code": code}, origin="https://elsewhere.example").status_code, 403)
        self.open_guest(url, code, guest)
        loaded = guest.get(url)
        self.assertContains(loaded, self.board.name)
        self.assertNotContains(loaded, self.project.name)
        self.assertNotContains(loaded, 'class="workspace-nav"')
        self.assertNotIn(code, str(dict(guest.session)))
        self.assertEqual(guest.get(url + "state/").status_code, 200)

    def test_view_link_cannot_write_and_edit_link_cannot_access_another_board(self):
        view_url, view_code = self.make_link("view")
        edit_url, edit_code = self.make_link()
        other_url, other_code = self.make_link(board=self.sibling)
        view = self.open_guest(view_url, view_code)
        edit = self.open_guest(edit_url, edit_code)
        self.assertNotContains(view.get(view_url), 'data-board-tool="note"')
        self.assertEqual(view.send(view_url + "change/", note(), as_json=True).status_code, 403)
        self.assertEqual(view.send(view_url + "upload/", {}).status_code, 403)
        self.assertEqual(edit.get(other_url + "state/").status_code, 404)
        self.assertEqual(edit.send(other_url, {"code": edit_code}).status_code, 404)
        self.assertEqual(edit.send(other_url + "change/", note(), as_json=True).status_code, 404)
        # A read-only grant on the same browser doesn't acquire edit rights.
        self.open_guest(view_url, view_code, guest=edit)
        self.assertEqual(edit.send(view_url + "change/", note(), as_json=True).status_code, 403)
        self.assertEqual(edit.get(self.sibling.get_absolute_url()).status_code, 302)
        self.assertEqual(self.open_guest(other_url, other_code).get(other_url + "state/").status_code, 200)
        self.assertFalse(BoardElement.objects.exists())

    def test_two_guests_merge_different_elements_and_stale_edit_is_rejected(self):
        url, code = self.make_link()
        first, second = self.open_guest(url, code), self.open_guest(url, code)
        change = url + "change/"
        initial = first.get(url + "state/").json()
        self.assertEqual(initial, {"revision": 0, "elements": []})
        a, b = note("Первая правка"), note("Вторая правка")
        response = first.send(change, a, as_json=True)
        self.assertEqual(response.status_code, 200)
        version = response.json()["element"]["revision"]
        self.assertEqual(second.send(change, b, as_json=True).status_code, 200)
        self.assertEqual([e["id"] for e in second.get(url + "state/", {"after": 0}).json()["elements"]],
                         [a["id"], b["id"]])
        winning = note("Новое название", key=a["id"], revision=version)
        self.assertEqual(second.send(change, winning, as_json=True).status_code, 200)
        stale = first.send(change, note("Устарело", key=a["id"], revision=version), as_json=True)
        self.assertEqual(stale.status_code, 409)
        latest = stale.json()["element"]
        self.assertEqual(latest["data"]["text"], "Новое название")
        self.assertEqual(first.send(change, {"id": a["id"], "revision": latest["revision"], "delete": True},
                                   as_json=True).status_code, 200)
        delta = second.get(url + "state/", {"after": latest["revision"]}).json()
        self.assertEqual([(e["id"], e["deleted"]) for e in delta["elements"]], [(a["id"], True)])
        self.assertEqual(first.send(change, a, as_json=True).status_code, 409)
        reopened = self.open_guest(url, code)
        self.assertEqual([e["id"] for e in reopened.get(url + "state/").json()["elements"]], [b["id"]])

    def test_arrow_follows_live_blocks_and_deletion_removes_only_attached_arrows(self):
        url, code = self.make_link()
        guest = self.open_guest(url, code)
        observer = self.open_guest(url, code)
        change = url + "change/"
        source, target, other = note(), note(x=270), note(x=520)
        for block in (source, target, other):
            self.assertEqual(guest.send(change, block, as_json=True).status_code, 200)
        attached, independent = arrow(source["id"], target["id"]), arrow(target["id"], other["id"])
        for connection in (attached, independent):
            self.assertEqual(guest.send(change, connection, as_json=True).status_code, 200)

        snapshot = observer.get(url + "state/").json()
        source_revision = next(item["revision"] for item in snapshot["elements"] if item["id"] == source["id"])
        # A move and resize only changes the block. Arrow geometry comes from the live endpoints.
        moved = note(key=source["id"], revision=source_revision, x=50, w=235, h=145)
        edited = guest.send(change, moved, as_json=True).json()["element"]
        delta = observer.get(url + "state/", {"after": snapshot["revision"]}).json()
        self.assertEqual(delta["elements"], [edited])
        self.assertEqual(self.board.elements.get(key=attached["id"]).data, attached["data"])

        delete = {"id": source["id"], "revision": edited["revision"], "delete": True}
        response = guest.send(change, delete, as_json=True)
        self.assertEqual(response.status_code, 200)
        delta = observer.get(url + "state/", {"after": edited["revision"]}).json()
        self.assertEqual(
            [(item["id"], item["deleted"], item["revision"]) for item in delta["elements"]],
            [(source["id"], True, delta["revision"]), (attached["id"], True, delta["revision"])],
        )
        self.assertFalse(self.board.elements.get(key=independent["id"]).deleted)
        self.assertEqual(guest.send(change, arrow(source["id"], target["id"]), as_json=True).status_code, 409)

    def test_arrow_endpoints_must_be_distinct_shapes_on_the_same_board(self):
        source, elsewhere = note(), note()
        endpoint = self.path + "change/"
        self.assertEqual(self.owner_client.send(endpoint, source, as_json=True).status_code, 200)
        self.assertEqual(self.owner_client.send(self.sibling.get_absolute_url() + "change/",
                                               elsewhere, as_json=True).status_code, 200)
        stroke = {"id": str(uuid.uuid4()), "revision": 0, "kind": "stroke",
                  "data": {"x": 0, "y": 0, "color": "ink", "points": [[0, 0], [10, 10]]}}
        self.assertEqual(self.owner_client.send(endpoint, stroke, as_json=True).status_code, 200)
        for target, expected in ((source["id"], 400), ("not-a-uuid", 400),
                                 (elsewhere["id"], 409), (stroke["id"], 409)):
            with self.subTest(expected=expected, target=target):
                attempt = self.owner_client.send(endpoint, arrow(source["id"], target), as_json=True)
                self.assertEqual(attempt.status_code, expected)
        self.assertFalse(self.board.elements.filter(kind="arrow").exists())

    def test_revoke_and_expiry_disable_previously_open_sessions(self):
        for expiry in (True, False):
            with self.subTest(expiry=expiry):
                url, code = self.make_link()
                guest = self.open_guest(url, code)
                link = self.board.links.get(pk=url.rstrip("/").rsplit("/", 1)[-1])
                if expiry:
                    link.expires_at = timezone.now() - timedelta(seconds=1)
                    link.save(update_fields=("expires_at",))
                else:
                    revoke = reverse("poker:board_link_revoke", args=(self.project.pk, self.board.pk, link.pk))
                    self.assertRedirects(self.owner_client.send(revoke, {}), self.path, fetch_redirect_response=False)
                for endpoint in (url, url + "state/", url + f"images/{uuid.uuid4()}/"):
                    response = guest.get(endpoint)
                    self.assertEqual(response.status_code, 404)
                    self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(guest.send(url + "change/", note(), as_json=True).status_code, 404)
                self.assertEqual(guest.send(url + "upload/", {}).status_code, 404)

    def test_image_is_reencoded_private_and_retry_does_not_duplicate_it(self):
        url, code = self.make_link()
        guest = self.open_guest(url, code)
        other_url, other_code = self.make_link("view", self.sibling)
        elsewhere = self.open_guest(other_url, other_code)
        key = str(uuid.uuid4())
        for _ in range(2):
            response = guest.send(url + "upload/", {"id": key, "x": -40, "y": 12, "image": self.make_image()})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["element"]["id"], key)
        self.assertEqual(self.board.elements.count(), 1)
        endpoint = f"images/{key}/"
        self.assertEqual(BoardClient().get(url + endpoint).status_code, 404)
        self.assertEqual(elsewhere.get(other_url + endpoint).status_code, 404)
        asset = guest.get(url + endpoint)
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset["Content-Type"], "image/png")
        self.assertIn("no-store", asset["Cache-Control"])
        self.assertEqual(asset["X-Content-Type-Options"], "nosniff")
        self.assertNotIn(b"user-supplied metadata", asset.content)
        with Image.open(io.BytesIO(asset.content)) as output:
            self.assertEqual(output.size, (45, 32))
            self.assertEqual(output.getpixel((0, 0)), (255, 0, 0, 255))
        self.assertEqual(self.owner_client.get(self.path + endpoint).content, asset.content)

    def test_invalid_shapes_images_and_cross_origin_edits_are_rejected(self):
        endpoint = self.path + "change/"
        for change in (
            note(w=9999), note(color="url(https://elsewhere.example)"), note(x=float("inf")),
            note(text="\u0000"), note(text="a" * 241), {**note(), "kind": "circle"},
            {"id": str(uuid.uuid4()), "revision": 0, "kind": "stroke",
             "data": {"x": 0, "y": 0, "points": [[0, 0]] * 257, "color": "ink"}},
        ):
            with self.subTest(kind=change["kind"]):
                response = self.owner_client.send(endpoint, json.dumps(change), as_json=True)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.owner_client.send(endpoint, note(), as_json=True, csrf=False).status_code, 403)
        for invalid in (
            SimpleUploadedFile("diagram.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/png"),
            SimpleUploadedFile("big.png", b"\0" * (2 * 1024 * 1024 + 1), "image/png"),
        ):
            self.assertEqual(self.owner_client.send(self.path + "upload/", {
                "id": str(uuid.uuid4()), "x": 0, "y": 0, "image": invalid,
            }).status_code, 400)
        self.board.refresh_from_db()
        self.assertEqual(self.board.revision, 0)
        self.assertFalse(BoardElement.objects.exists())


@skipUnless(connection.vendor == "postgresql", "Exercises PostgreSQL row locks")
@override_settings(ALLOWED_HOSTS=["testserver"])
class ConcurrentBoardTests(TransactionTestCase):
    def test_concurrent_posts_get_distinct_revisions_and_are_in_the_delta(self):
        owner = get_user_model().objects.create_user("board-lock-test")
        project = Project.objects.create(owner=owner, name="Параллельная работа")
        board = Board.objects.create(project=project, name="Доска")
        path = board.get_absolute_url()
        pair = [note("Один"), note("Два")]
        start = Barrier(2)

        def write(change):
            try:
                client = BoardClient()
                client.force_login(owner)
                self.assertEqual(client.get(path).status_code, 200)
                start.wait(timeout=10)
                reply = client.send(path + "change/", change, as_json=True)
                self.assertEqual(reply.status_code, 200)
                return reply.json()["element"]["revision"]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(write, pair)), [1, 2])
        client = BoardClient()
        client.force_login(owner)
        snapshot = client.get(path + "state/", {"after": 0}).json()
        self.assertEqual(snapshot["revision"], 2)
        self.assertCountEqual([element["id"] for element in snapshot["elements"]], [change["id"] for change in pair])
