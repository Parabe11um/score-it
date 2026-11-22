(() => {
    "use strict";

    const invite = document.querySelector("[data-board-invitation]");
    if (invite) {
        const code = location.hash.slice(1);
        history.replaceState(null, "", location.pathname);
        if (/^[A-Za-z0-9_-]{43}$/.test(code)) {
            invite.elements.code.value = code;
            invite.requestSubmit();
        } else {
            document.querySelector("[data-board-invitation-status]").textContent =
                "Нет ключа доступа. Откройте полную ссылку, которую прислал организатор.";
        }
        return;
    }

    const copy = document.querySelector("[data-board-copy]");
    copy?.addEventListener("click", async () => {
        const link = document.querySelector("[data-board-share-url]");
        const feedback = document.querySelector("[data-board-copy-feedback]");
        try {
            await navigator.clipboard.writeText(link.value);
            feedback.textContent = "Ссылка скопирована.";
        } catch {
            link.focus();
            link.select();
            feedback.textContent = "Скопируйте выделенную ссылку.";
        }
    });

    const board = document.querySelector("[data-board]");
    if (!board) return;
    const find = (name) => board.querySelector("[data-board-" + name + "]");
    const canvas = find("canvas");
    const backdrop = find("backdrop");
    const scene = find("elements");
    const preview = find("preview");
    const controls = find("controls");
    const status = find("status");
    const hint = find("hint");
    const empty = find("empty");
    const retry = find("retry");
    const properties = find("properties");
    const label = find("label");
    const color = find("color");
    const csrf = board.querySelector("[name=csrfmiddlewaretoken]")?.value;
    const canEdit = board.dataset.boardEdit === "true";
    if (board.dataset.boardGuest === "true" && location.hash) {
        history.replaceState(null, "", location.pathname);
    }

    const ns = "http://www.w3.org/2000/svg";
    const minZoom = 0.05;
    const shapeKinds = new Set(["circle", "square", "rectangle", "note"]);
    const palette = {
        ink: ["#334155", "#f1f5f9"],
        mint: ["#076d35", "#dcfce7"],
        blue: ["#1d4ed8", "#dbeafe"],
        yellow: ["#854d0e", "#fef3c7"],
        pink: ["#9d174d", "#fce7f3"],
    };
    const hints = {
        hand: "Перетаскивайте полотно. Масштаб меняется кнопками + и −.",
        select: "Нажмите на элемент, чтобы выбрать его, или перетащите, чтобы переместить.",
        stroke: "Нажмите и ведите по полотну, чтобы нарисовать линию.",
        circle: "Щёлкните, чтобы добавить круг, или потяните, чтобы задать размер.",
        square: "Щёлкните, чтобы добавить квадрат, или потяните, чтобы задать размер.",
        rectangle: "Щёлкните, чтобы добавить прямоугольник, или потяните, чтобы задать размер.",
        note: "Щёлкните в том месте, где нужен стикер, затем напишите текст.",
    };
    const items = new Map();
    const nodes = new Map();
    let tool = canEdit ? "select" : "hand";
    let selected = null;
    let selectionRevision = null;
    let gesture = null;
    let cursor = null;
    let polling = false;
    let pollTimer = null;
    let closed = false;
    let busy = false;
    let failedWrite = null;
    let writeNotice = null;
    let offline = false;
    let leaveGuard = false;
    let left = 0;
    let top = 0;
    let zoom = 1;

    const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
    const coord = (n) => Math.round(clamp(n, -100000, 100000) * 10) / 10;
    const size = (n) => Math.round(clamp(n, 16, 4000) * 10) / 10;
    const svg = (name, attributes = {}) => {
        const node = document.createElementNS(ns, name);
        for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
        return node;
    };
    const uuid = () => {
        if (crypto.randomUUID) return crypto.randomUUID();
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 15) | 64;
        bytes[8] = (bytes[8] & 63) | 128;
        const h = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
        return [h.slice(0, 8), h.slice(8, 12), h.slice(12, 16), h.slice(16, 20), h.slice(20)].join("-");
    };
    const dims = () => ({w: canvas.clientWidth || 1, h: canvas.clientHeight || 1});
    const dots = svg("pattern", {id: "board-grid", width: 24, height: 24, patternUnits: "userSpaceOnUse"});
    dots.append(svg("circle", {cx: 1.5, cy: 1.5, r: 1, fill: "#ccd5d0"}));
    const defs = svg("defs");
    defs.append(dots);
    const grid = svg("rect", {fill: "url(#board-grid)"});
    backdrop.append(defs, grid);

    function placeView() {
        const {w, h} = dims();
        canvas.setAttribute("viewBox", [left, top, w / zoom, h / zoom].join(" "));
        grid.setAttribute("x", left);
        grid.setAttribute("y", top);
        grid.setAttribute("width", w / zoom);
        grid.setAttribute("height", h / zoom);
        board.querySelector('[data-board-zoom="reset"]').textContent = Math.round(zoom * 100) + "%";
    }
    function zoomAt(factor, x, y) {
        const next = clamp(zoom * factor, minZoom, 3);
        left = coord(left + x / zoom - x / next);
        top = coord(top + y / zoom - y / next);
        zoom = next;
        placeView();
    }

    function wrappedText(value, columns, rows) {
        const lines = [];
        for (const paragraph of value.split("\n")) {
            let line = "";
            for (let word of paragraph.trim().split(/\s+/)) {
                if (line && Array.from(line + " " + word).length <= columns) {
                    line += " " + word;
                    continue;
                }
                if (line) lines.push(line);
                let chars = Array.from(word);
                while (chars.length > columns) {
                    lines.push(chars.slice(0, columns).join(""));
                    chars = chars.slice(columns);
                }
                line = chars.join("");
            }
            lines.push(line);
        }
        if (lines.length > rows) {
            lines.length = rows;
            lines[rows - 1] = Array.from(lines[rows - 1]).slice(0, columns - 1).join("") + "…";
        }
        return lines;
    }

    function drawItem(item, ghost = false) {
        const {kind, data: d, id} = item;
        const g = svg("g", {transform: "translate(" + d.x + " " + d.y + ")"});
        if (!ghost) {
            g.setAttribute("data-element-id", id);
            g.setAttribute("data-element-kind", kind);
        }
        if (kind === "image") {
            if (ghost) {
                g.append(svg("rect", {width: d.w, height: d.h, fill: "#e7f9ee", stroke: "#076d35"}));
            } else {
                g.append(svg("rect", {width: d.w, height: d.h, fill: "#eef2ef", stroke: "#ccd5d0"}));
                g.append(svg("image", {width: d.w, height: d.h, href: board.dataset.imageBase + id + "/"}));
            }
            return g;
        }
        const [stroke, fill] = palette[d.color] || palette.ink;
        if (kind === "stroke") {
            const points = d.points.map((point) => point.join(",")).join(" ");
            // A wider transparent line makes pencil marks easier to select.
            if (!ghost) g.append(svg("polyline", {points, fill: "none", stroke: "transparent", "stroke-width": 12}));
            g.append(svg("polyline", {points, fill: "none", stroke, "stroke-width": 3,
                "stroke-linecap": "round", "stroke-linejoin": "round"}));
            return g;
        }
        if (kind === "circle") {
            g.append(svg("circle", {cx: d.w / 2, cy: d.h / 2, r: d.w / 2, fill, stroke, "stroke-width": 2}));
        } else {
            g.append(svg("rect", {width: d.w, height: d.h, rx: kind === "note" ? 5 : 2, fill, stroke, "stroke-width": 2}));
        }
        if (d.text) {
            const title = svg("title");
            title.textContent = d.text;
            g.append(title);
            const ratio = kind === "circle" ? 0.68 : 1;
            const w = Math.max(1, d.w * ratio - 12);
            const h = Math.max(1, d.h * ratio - 8);
            const area = svg("svg", {x: (d.w - w) / 2, y: (d.h - h) / 2, width: w, height: h,
                overflow: "hidden", "pointer-events": "none"});
            const text = svg("text", {"text-anchor": "middle", "dominant-baseline": "middle",
                fill: "#212a34", "font-size": 14, "font-weight": kind === "note" ? 500 : 650});
            const lines = wrappedText(d.text, Math.max(1, Math.floor(w / 8)), Math.max(1, Math.floor(h / 17)));
            lines.forEach((line, i) => {
                const span = svg("tspan", {x: w / 2, y: h / 2 + (i - (lines.length - 1) / 2) * 17});
                span.textContent = line;
                text.append(span);
            });
            area.append(text);
            g.append(area);
        }
        return g;
    }

    function bounds(item) {
        const d = item.data;
        if (item.kind !== "stroke") return {x: d.x, y: d.y, w: d.w, h: d.h};
        const xs = d.points.map((p) => p[0]);
        const ys = d.points.map((p) => p[1]);
        return {x: d.x + Math.min(...xs), y: d.y + Math.min(...ys),
            w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys)};
    }

    function fitBoard() {
        const boxes = Array.from(items.values()).filter((item) => !item.deleted).map(bounds);
        if (!boxes.length) return;
        const x = Math.min(...boxes.map((box) => box.x));
        const y = Math.min(...boxes.map((box) => box.y));
        const width = Math.max(...boxes.map((box) => box.x + box.w)) - x;
        const height = Math.max(...boxes.map((box) => box.y + box.h)) - y;
        const {w, h} = dims();
        zoom = clamp(Math.min(w / (width + 80), h / (height + 80)), minZoom, 1);
        left = coord(x + (width - w / zoom) / 2);
        top = coord(y + (height - h / zoom) / 2);
        placeView();
    }

    function paintPreview() {
        preview.replaceChildren();
        const draft = gesture?.draft;
        if (draft) preview.append(drawItem(draft, true));
        const item = (gesture?.mode === "move" ? draft : items.get(selected));
        if (item && !item.deleted) {
            const {x, y, w, h} = bounds(item);
            preview.append(svg("rect", {x: x - 4, y: y - 4, width: w + 8, height: h + 8,
                fill: "none", stroke: "#076d35", "stroke-dasharray": "5 4",
                "stroke-width": 1.5, "vector-effect": "non-scaling-stroke"}));
        }
        empty.hidden = cursor === null || nodes.size > 0 || Boolean(draft) || closed;
    }

    function merge(item) {
        if (items.get(item.id)?.revision >= item.revision) return;
        const existing = nodes.get(item.id);
        const prev = items.get(item.id);
        items.set(item.id, item);
        if (item.deleted) {
            existing?.remove();
            nodes.delete(item.id);
            return;
        }
        if (existing && item.kind === "image" && prev.data.w === item.data.w && prev.data.h === item.data.h) {
            existing.setAttribute("transform", "translate(" + item.data.x + " " + item.data.y + ")");
            return;
        }
        const node = drawItem(item);
        if (existing) {
            existing.replaceWith(node);
        } else {
            let next = null;
            for (const child of scene.children) {
                if (items.get(child.dataset.elementId).order > item.order) {
                    next = child;
                    break;
                }
            }
            scene.insertBefore(node, next);
        }
        nodes.set(item.id, node);
    }

    function updateProperties(force = false) {
        if (!canEdit) return;
        const item = items.get(selected);
        if (!item || item.deleted) {
            selected = null;
            selectionRevision = null;
            properties.hidden = true;
            return;
        }
        const shape = shapeKinds.has(item.kind);
        properties.hidden = false;
        find("label-fields").hidden = !shape;
        find("save-label").hidden = !shape;
        if (force || document.activeElement !== label && selectionRevision !== item.revision) {
            label.value = item.data.text || "";
            selectionRevision = item.revision;
        }
    }

    function guardLeave(event) {
        event.preventDefault();
        event.returnValue = "";
    }

    function showStatus() {
        const message = closed ? ["Доступ к доске закрыт.", true]
            : busy ? ["Сохраняем…", false]
            : failedWrite ? ["Правка не сохранена. Повторите отправку.", true]
            : writeNotice || (offline ? ["Нет связи. Переподключаемся…", true]
                : cursor === null ? ["Загружаем доску…", false] : ["Доска обновлена", false]);
        if (status.textContent !== message[0]) status.textContent = message[0];
        status.dataset.error = String(message[1]);
        retry.hidden = closed || !failedWrite || busy;
        const protect = !closed && (busy || Boolean(failedWrite));
        if (protect !== leaveGuard) {
            leaveGuard = protect;
            if (protect) window.addEventListener("beforeunload", guardLeave);
            else window.removeEventListener("beforeunload", guardLeave);
        }
    }

    function closeBoard() {
        closed = true;
        failedWrite = null;
        gesture = null;
        items.clear();
        nodes.clear();
        scene.replaceChildren();
        controls.hidden = true;
        hint.hidden = true;
        if (properties) properties.hidden = true;
        paintPreview();
        showStatus();
    }

    async function poll() {
        if (polling || closed || document.hidden) return;
        polling = true;
        try {
            const suffix = cursor === null ? "" : "?after=" + cursor;
            const response = await fetch(board.dataset.stateUrl + suffix, {
                cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(10000),
            });
            if (response.status === 403 || response.status === 404) {
                closeBoard();
                return;
            }
            if (!response.ok) throw new Error("poll");
            const data = await response.json();
            if (closed) return;
            data.elements.forEach(merge);
            cursor = data.revision;
            updateProperties();
            paintPreview();
            offline = false;
        } catch {
            offline = true;
        } finally {
            polling = false;
            showStatus();
            if (!closed && !document.hidden) pollTimer = setTimeout(poll, offline ? 3500 : 1200);
        }
    }

    async function send(job) {
        if (!canEdit || cursor === null || closed || busy) return;
        busy = true;
        failedWrite = null;
        writeNotice = null;
        showStatus();
        try {
            const response = await fetch(job.url, {
                method: "POST", body: job.body, credentials: "same-origin",
                headers: {"X-CSRFToken": csrf, ...job.headers}, signal: AbortSignal.timeout(15000),
            });
            if (closed) return;
            if (response.status === 403 || response.status === 404) {
                closeBoard();
                return;
            }
            let data = null;
            if ((response.headers.get("content-type") || "").includes("application/json")) {
                data = await response.json();
            }
            if (response.ok && data?.element) {
                merge(data.element);
                if (job.select) {
                    selected = data.element.id;
                    setTool("select");
                }
                updateProperties(true);
                if (job.focusLabel && selected && !label?.parentElement.hidden) label.focus();
            } else if (response.status >= 400 && response.status < 500) {
                if (data?.element) merge(data.element);
                updateProperties(true);
                writeNotice = [data?.error || "Правка не принята. Проверьте данные.", true];
            } else {
                throw new Error("write");
            }
        } catch {
            if (!closed) failedWrite = job;
        } finally {
            busy = false;
            paintPreview();
            showStatus();
        }
    }

    function change(payload, select = false) {
        return send({url: board.dataset.changeUrl, body: JSON.stringify(payload),
            headers: {"Content-Type": "application/json"}, select, focusLabel: select && payload.kind === "note"});
    }

    function setTool(next) {
        tool = next;
        canvas.dataset.tool = next;
        hint.textContent = hints[next];
        board.querySelectorAll("[data-board-tool]").forEach((button) => {
            button.setAttribute("aria-pressed", String(button.dataset.boardTool === next));
        });
        if (next !== "select") {
            selected = null;
            updateProperties();
            paintPreview();
        }
    }

    function point(event) {
        const rect = canvas.getBoundingClientRect();
        return {x: coord(left + (event.clientX - rect.left) / zoom),
            y: coord(top + (event.clientY - rect.top) / zoom)};
    }

    function select(id) {
        const old = selected;
        selected = id || null;
        updateProperties(old !== selected);
        paintPreview();
    }

    function makeShape(g, p) {
        const dx = p.x - g.start.x;
        const dy = p.y - g.start.y;
        const dragged = Math.max(Math.abs(dx), Math.abs(dy)) >= 8;
        const square = g.mode === "circle" || g.mode === "square";
        const edge = size(Math.max(Math.abs(dx), Math.abs(dy)));
        const w = dragged ? (square ? edge : size(Math.abs(dx))) : (square ? 112 : 170);
        const h = dragged ? (square ? edge : size(Math.abs(dy))) : (square ? 112 : 108);
        return {kind: g.mode, data: {x: coord(g.start.x - (dragged && dx < 0 ? w : 0)),
            y: coord(g.start.y - (dragged && dy < 0 ? h : 0)), w, h, color: g.color, text: ""}};
    }

    function updateGesture(event) {
        const g = gesture;
        if (!g || g.pointer !== event.pointerId) return;
        if (g.mode === "hand") {
            left = coord(g.left + (g.screenX - event.clientX) / zoom);
            top = coord(g.top + (g.screenY - event.clientY) / zoom);
            placeView();
            return;
        }
        const p = point(event);
        if (g.mode === "move") {
            const d = g.source.data;
            if (!g.draft && Math.hypot(p.x - g.start.x, p.y - g.start.y) < 2 / zoom) return;
            g.draft = {...g.source, data: {...d, x: coord(d.x + p.x - g.start.x), y: coord(d.y + p.y - g.start.y)}};
        } else if (g.mode === "stroke") {
            const points = g.draft.data.points;
            const next = [coord(p.x - g.start.x), coord(p.y - g.start.y)];
            const last = points[points.length - 1];
            if (Math.hypot(last[0] - next[0], last[1] - next[1]) >= 1) {
                if (points.length < 256) points.push(next);
                else points[255] = next;
            }
        } else {
            g.draft = makeShape(g, p);
        }
        paintPreview();
    }

    canvas.addEventListener("pointerdown", (event) => {
        if (!event.isPrimary || event.button !== 0 || gesture || closed) return;
        const p = point(event);
        if (tool === "hand") {
            gesture = {mode: "hand", left, top, screenX: event.clientX, screenY: event.clientY};
        } else if (canEdit && cursor !== null && !busy && !failedWrite) {
            if (tool === "select") {
                select(event.target.closest("[data-element-id]")?.dataset.elementId);
                if (!selected) return;
                gesture = {mode: "move", source: items.get(selected), start: p};
            } else {
                const ink = color.value === "ink" && tool === "note" ? "yellow" : color.value;
                gesture = {mode: tool, start: p, color: ink};
                gesture.draft = tool === "stroke"
                    ? {kind: "stroke", data: {...p, color: ink, points: [[0, 0]]}} : makeShape(gesture, p);
            }
        }
        if (gesture) {
            gesture.pointer = event.pointerId;
            canvas.setPointerCapture(event.pointerId);
            event.preventDefault();
            paintPreview();
        }
    });
    canvas.addEventListener("pointermove", updateGesture);
    canvas.addEventListener("pointerup", (event) => {
        if (!gesture || gesture.pointer !== event.pointerId) return;
        updateGesture(event);
        const {draft, mode, source} = gesture;
        gesture = null;
        if (draft) {
            if (draft.kind === "stroke" && draft.data.points.length === 1) draft.data.points.push([0.1, 0]);
            change({id: source?.id || uuid(), revision: source?.revision || 0, kind: draft.kind, data: draft.data},
                mode !== "move" && draft.kind !== "stroke");
        }
        paintPreview();
    });
    for (const type of ["pointercancel", "lostpointercapture"]) {
        canvas.addEventListener(type, () => {
            gesture = null;
            paintPreview();
        });
    }
    canvas.addEventListener("wheel", (event) => {
        if (gesture || closed || !event.ctrlKey && !event.metaKey) return;
        event.preventDefault();
        const rect = canvas.getBoundingClientRect();
        zoomAt(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX - rect.left, event.clientY - rect.top);
    }, {passive: false});
    board.querySelectorAll("[data-board-tool]").forEach((button) =>
        button.addEventListener("click", () => setTool(button.dataset.boardTool)));
    board.querySelectorAll("[data-board-zoom]").forEach((button) => button.addEventListener("click", () => {
        if (gesture || closed) return;
        if (button.dataset.boardZoom === "fit") {
            fitBoard();
        } else if (button.dataset.boardZoom === "reset") {
            left = 0; top = 0; zoom = 1;
            placeView();
        } else {
            const {w, h} = dims();
            zoomAt(button.dataset.boardZoom === "in" ? 1.25 : 0.8, w / 2, h / 2);
        }
    }));

    if (canEdit) {
        properties.addEventListener("submit", (event) => {
            event.preventDefault();
            const item = items.get(selected);
            if (!item || item.deleted || !shapeKinds.has(item.kind) || busy || failedWrite) return;
            change({id: item.id, revision: selectionRevision, kind: item.kind,
                data: {...item.data, text: label.value}});
        });
        find("delete").addEventListener("click", () => {
            const item = items.get(selected);
            if (!item || item.deleted || busy || failedWrite) return;
            change({id: item.id, revision: item.revision, delete: true});
        });
        const file = find("file");
        find("upload").addEventListener("click", () => {
            if (!busy && !failedWrite && cursor !== null) file.click();
        });
        file.addEventListener("change", () => {
            const image = file.files[0];
            file.value = "";
            if (!image || busy || failedWrite) return;
            if (image.size > 2 * 1024 * 1024) {
                writeNotice = ["Выберите PNG или JPG до 2 МБ.", true];
                showStatus();
                return;
            }
            const {w, h} = dims();
            const body = new FormData();
            body.append("id", uuid());
            body.append("image", image);
            body.append("x", String(coord(left + w / (2 * zoom) - 80)));
            body.append("y", String(coord(top + h / (2 * zoom) - 60)));
            send({url: board.dataset.uploadUrl, body, select: true});
        });
    }

    retry.addEventListener("click", () => { if (failedWrite) send(failedWrite); });
    document.addEventListener("visibilitychange", () => {
        clearTimeout(pollTimer);
        if (!document.hidden) poll();
    });
    new ResizeObserver(placeView).observe(canvas);
    setTool(tool);
    placeView();
    poll();
})();
