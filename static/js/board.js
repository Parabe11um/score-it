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
    const connections = find("connections");
    const scene = find("elements");
    const preview = find("preview");
    const handles = find("handles");
    const controls = find("controls");
    const status = find("status");
    const hint = find("hint");
    const empty = find("empty");
    const retry = find("retry");
    const undoButton = find("undo");
    const redoButton = find("redo");
    const hasHistory = Boolean(undoButton && redoButton && board.dataset.historyUrl);
    const properties = find("properties");
    const inlineText = find("inline-text");
    const label = find("label");
    const color = find("color");
    const csrf = board.querySelector("[name=csrfmiddlewaretoken]")?.value;
    const canEdit = board.dataset.boardEdit === "true";
    if (board.dataset.boardGuest === "true" && location.hash) {
        history.replaceState(null, "", location.pathname);
    }

    const ns = "http://www.w3.org/2000/svg";
    const minZoom = 0.05;
    const historyLimit = 30;
    const isShape = (item) => ["circle", "square", "rectangle", "note"].includes(item?.kind);
    const palette = {
        ink: ["#334155", "#f1f5f9"],
        mint: ["#076d35", "#dcfce7"],
        blue: ["#1d4ed8", "#dbeafe"],
        yellow: ["#854d0e", "#fef3c7"],
        pink: ["#9d174d", "#fce7f3"],
    };
    const hints = {
        hand: "Перетаскивайте полотно. Масштаб меняется кнопками + и −.",
        select: "Перетащите блок, чтобы переместить. Двойной щелчок — подпись; Delete — удаление.",
        stroke: "Нажмите и ведите по полотну, чтобы нарисовать линию.",
        arrow: "Перетащите стрелку или укажите начало и конец двумя щелчками. Фигуры рядом не обязательны.",
        circle: "Щёлкните, чтобы добавить круг, или потяните, чтобы задать размер.",
        square: "Щёлкните, чтобы добавить квадрат, или потяните, чтобы задать размер.",
        rectangle: "Щёлкните, чтобы добавить прямоугольник, или потяните, чтобы задать размер.",
        note: "Щёлкните в том месте, где нужен стикер, затем напишите текст.",
    };
    const items = new Map();
    const nodes = new Map();
    const arrows = new Map();
    const undo = [];
    const redo = [];
    const replayVersions = new Map();
    let tool = canEdit ? "select" : "hand";
    let selected = null;
    let editing = null;
    let obscured = null;
    let arrowStart = null;
    let arrowTracking = null;
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
        paintPreview();
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

    function labelBox(item) {
        const {x, y, w, h} = item.data;
        const ratio = item.kind === "circle" ? 0.69 : 1;
        const width = Math.max(1, w * ratio - 10);
        const height = Math.max(1, h * ratio - 8);
        return {x: x + (w - width) / 2, y: y + (h - height) / 2, width, height};
    }

    function drawItem(item, ghost = false, withoutLabel = false) {
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
        if (d.text && !withoutLabel) {
            const title = svg("title");
            title.textContent = d.text;
            g.append(title);
            const {x, y, width: w, height: h} = labelBox(item);
            const area = svg("svg", {x: x - d.x, y: y - d.y, width: w, height: h,
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
        if (item.kind === "arrow") {
            const geometry = arrowGeometry(d);
            if (!geometry) return null;
            const {start, end} = geometry;
            return {x: Math.min(start.x, end.x), y: Math.min(start.y, end.y),
                w: Math.abs(end.x - start.x), h: Math.abs(end.y - start.y)};
        }
        if (item.kind !== "stroke") return {x: d.x, y: d.y, w: d.w, h: d.h};
        const xs = d.points.map((p) => p[0]);
        const ys = d.points.map((p) => p[1]);
        return {x: d.x + Math.min(...xs), y: d.y + Math.min(...ys),
            w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys)};
    }

    function fitBoard() {
        const boxes = Array.from(items.values()).filter((item) => !item.deleted)
            .map((item) => bounds(visibleItem(item.id))).filter(Boolean);
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

    function visibleItem(id) {
        const item = items.get(id);
        if (!item || item.deleted) return null;
        if (gesture?.draft?.id === id) return gesture.draft;
        if (editing?.id === id) return {...item, data: editing.data};
        return item;
    }

    function center(item) {
        return {x: item.data.x + item.data.w / 2, y: item.data.y + item.data.h / 2};
    }

    function edgeToward(item, toward) {
        const c = center(item);
        const dx = toward.x - c.x;
        const dy = toward.y - c.y;
        const {w, h} = item.data;
        const scale = item.kind === "circle" ? Math.hypot(dx, dy) / (w / 2)
            : Math.max(Math.abs(dx) / (w / 2), Math.abs(dy) / (h / 2));
        return scale ? {x: c.x + dx / scale, y: c.y + dy / scale} : {x: c.x + w / 2, y: c.y};
    }

    function shapeAt(p, except = null, tolerance = 0) {
        let best = null;
        let distance = tolerance;
        for (const stored of items.values()) {
            if (!isShape(stored) || stored.deleted || stored.id === except) continue;
            const item = visibleItem(stored.id);
            const c = center(item);
            const dx = Math.abs(p.x - c.x);
            const dy = Math.abs(p.y - c.y);
            const gap = item.kind === "circle" ? Math.max(0, Math.hypot(dx, dy) - item.data.w / 2)
                : Math.hypot(Math.max(0, dx - item.data.w / 2), Math.max(0, dy - item.data.h / 2));
            if (gap < distance || gap === distance && (!best || item.order > best.order)) {
                best = item;
                distance = gap;
            }
        }
        return best;
    }

    function snap(p, except = null) {
        return {p, id: shapeAt(p, except, 12 / zoom)?.id || null};
    }

    function withArrowEnd(data, side, anchor) {
        const next = {...data, [side]: anchor.id};
        const field = side === "source" ? "start" : "end";
        if (anchor.id) delete next[field];
        else next[field] = [anchor.p.x, anchor.p.y];
        return next;
    }

    function arrowAnchor(data, side) {
        if (data[side]) {
            const shape = visibleItem(data[side]);
            return isShape(shape) ? {shape, p: center(shape)} : null;
        }
        const [x, y] = data[side === "source" ? "start" : "end"];
        return {p: {x, y}};
    }

    function arrowGeometry(data) {
        const from = arrowAnchor(data, "source");
        const to = arrowAnchor(data, "target");
        return from && to ? {
            start: from.shape ? edgeToward(from.shape, to.p) : from.p,
            end: to.shape ? edgeToward(to.shape, from.p) : to.p,
        } : null;
    }

    function positionArrow(parts, start, end, color, highlighted = false) {
        const signature = [start.x, start.y, end.x, end.y, zoom, color, highlighted].join("/");
        if (parts.signature === signature) return;
        parts.signature = signature;
        const dx = end.x - start.x;
        const dy = end.y - start.y;
        const length = Math.hypot(dx, dy) || 1;
        const size = Math.min(length * .6, 11 / zoom);
        const ux = dx / length;
        const uy = dy / length;
        const x = end.x - ux * size;
        const y = end.y - uy * size;
        const tip = [end.x, end.y, x + uy * size * .45, y - ux * size * .45,
            x - uy * size * .45, y + ux * size * .45];
        for (const line of [parts.line, parts.hit].filter(Boolean)) {
            line.setAttribute("x1", start.x);
            line.setAttribute("y1", start.y);
            line.setAttribute("x2", end.x);
            line.setAttribute("y2", end.y);
        }
        parts.line.setAttribute("stroke", color);
        parts.line.setAttribute("stroke-width", highlighted ? 3.2 : 2);
        parts.head.setAttribute("fill", color);
        parts.head.setAttribute("points", tip.join(" "));
    }

    function arrowParts(id) {
        const node = svg("g", id ? {"data-element-id": id, "data-element-kind": "arrow"} : {});
        const hit = id && svg("line", {stroke: "transparent", "stroke-width": 14,
            "vector-effect": "non-scaling-stroke", "pointer-events": "stroke"});
        const line = svg("line", {"vector-effect": "non-scaling-stroke", "stroke-linecap": "round"});
        const head = svg("polygon");
        if (hit) node.append(hit);
        node.append(line, head);
        return {node, hit, line, head};
    }

    function drawConnections() {
        for (const {item, parts} of arrows.values()) {
            const data = visibleItem(item.id).data;
            const geometry = arrowGeometry(data);
            parts.node.toggleAttribute("hidden", !geometry);
            if (!geometry) continue;
            const selectedArrow = selected === item.id;
            positionArrow(parts, geometry.start, geometry.end,
                selectedArrow ? "#076d35" : palette[data.color][0], selectedArrow);
        }
    }

    function highlight(item, attributes) {
        const {x, y, w, h} = bounds(item);
        const pad = 4 / zoom;
        const props = {fill: "none", stroke: "#076d35", "stroke-width": 1.5,
            "vector-effect": "non-scaling-stroke", ...attributes};
        return item.kind === "circle"
            ? svg("circle", {cx: x + w / 2, cy: y + h / 2, r: w / 2 + pad, ...props})
            : svg("rect", {x: x - pad, y: y - pad, width: w + 2 * pad, height: h + 2 * pad, ...props});
    }

    function showArrowPreview() {
        const drawing = gesture?.mode === "arrow" ? gesture : null;
        const from = drawing?.from || arrowStart;
        const to = drawing?.to || arrowTracking;
        const source = visibleItem(from?.id);
        const target = visibleItem(to?.id);
        if (target) preview.append(highlight(target, {"data-board-snap": "target", "stroke-width": 2.5}));
        if (source) preview.append(highlight(source, {"data-board-snap": "source"}));
        if (!from || !to) return;
        const data = withArrowEnd(withArrowEnd({}, "source", from), "target", to);
        const geometry = arrowGeometry(data);
        if (!geometry) return;
        const parts = arrowParts();
        positionArrow(parts, geometry.start, geometry.end, "#076d35");
        preview.append(parts.node);
    }

    function drawGrip(x, y, name, part, attached = false) {
        const r = 5 / zoom;
        handles.append(svg("circle", {cx: x, cy: y, r: r * 2.4, fill: "transparent", [name]: part}));
        handles.append(svg("circle", {cx: x, cy: y, r, fill: attached ? "#dcfce7" : "#fff", stroke: "#076d35",
            "vector-effect": "non-scaling-stroke", "stroke-width": 1.5, "pointer-events": "none"}));
    }

    function paintPreview() {
        preview.replaceChildren();
        handles.replaceChildren();
        const textItem = editing && visibleItem(editing.id);
        const draft = gesture?.draft?.kind === "arrow" ? null : gesture?.draft || textItem;
        if (obscured && obscured !== draft?.id) nodes.get(obscured)?.removeAttribute("visibility");
        obscured = draft?.id || null;
        if (draft) {
            nodes.get(obscured)?.setAttribute("visibility", "hidden");
            preview.append(drawItem(draft, true, textItem?.id === draft.id));
        }
        if (inlineText) {
            inlineText.toggleAttribute("hidden", !textItem);
            if (textItem) {
                for (const [key, val] of Object.entries(labelBox(textItem))) inlineText.setAttribute(key, val);
            }
        }
        const item = visibleItem(selected);
        if (item?.kind === "arrow" && tool === "select") {
            const geometry = arrowGeometry(item.data);
            if (geometry) {
                drawGrip(geometry.start.x, geometry.start.y, "data-board-arrow-end", "source", Boolean(item.data.source));
                drawGrip(geometry.end.x, geometry.end.y, "data-board-arrow-end", "target", Boolean(item.data.target));
            }
            const target = visibleItem(gesture?.snap);
            if (target) preview.append(highlight(target, {"data-board-snap": "target", "stroke-width": 2.5}));
        } else if (item) {
            preview.append(highlight(item, {"stroke-dasharray": "5 4"}));
            if (canEdit && isShape(item)) {
                const d = item.data;
                for (const [side, x, y] of [["e", d.x + d.w, d.y + d.h / 2], ["s", d.x + d.w / 2, d.y + d.h],
                    ["se", d.x + d.w, d.y + d.h]]) {
                    drawGrip(x, y, "data-board-resize", side);
                }
            }
        }
        drawConnections();
        if (tool === "arrow") showArrowPreview();
        empty.hidden = cursor === null || nodes.size > 0 || arrows.size > 0 || Boolean(draft) || closed;
    }

    function merge(item) {
        if (items.get(item.id)?.revision >= item.revision) return;
        const existing = nodes.get(item.id);
        const prev = items.get(item.id);
        items.set(item.id, item);
        if (item.kind === "arrow") {
            const entry = arrows.get(item.id);
            if (item.deleted) {
                entry?.parts.node.remove();
                arrows.delete(item.id);
            } else if (entry) {
                entry.item = item;
            } else {
                const parts = arrowParts(item.id);
                connections.append(parts.node);
                arrows.set(item.id, {item, parts});
            }
            return;
        }
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

    function updateProperties() {
        if (!canEdit) return;
        const item = items.get(selected);
        if (!item || item.deleted) {
            selected = null;
            editing = null;
            properties.hidden = true;
            return;
        }
        properties.hidden = false;
        find("edit-label").hidden = !isShape(item) || Boolean(editing);
        find("save-label").hidden = !editing;
        find("delete").hidden = Boolean(editing);
    }

    function canMutate() {
        return canEdit && cursor !== null && !closed && !busy && !failedWrite;
    }

    function beginLabel(id) {
        const item = items.get(id);
        if (!canMutate() || editing || gesture || !isShape(item) || item.deleted) return;
        selected = id;
        editing = {id, revision: item.revision, kind: item.kind, data: {...item.data}, original: item.data};
        label.value = item.data.text;
        writeNotice = null;
        updateHint();
        updateProperties();
        paintPreview();
        showStatus();
        label.focus({preventScroll: true});
        label.setSelectionRange(label.value.length, label.value.length);
    }

    function labelChanged() {
        return editing && (label.value !== editing.original.text ||
            editing.data.w !== editing.original.w || editing.data.h !== editing.original.h);
    }

    function finishLabel(cancel = false) {
        if (!editing) return true;
        if (gesture || !canMutate()) return false;
        if (!cancel && (label.value.split("\n").length > 6 || /[\x00-\x09\x0b-\x1f]/.test(label.value))) {
            writeNotice = ["Подпись может содержать до шести строк обычного текста.", true];
            showStatus();
            label.focus({preventScroll: true});
            return false;
        }
        const edit = editing;
        const save = !cancel && labelChanged();
        editing = null;
        writeNotice = null;
        updateHint();
        updateProperties();
        paintPreview();
        canvas.focus({preventScroll: true});
        if (save) {
            change({id: edit.id, revision: edit.revision, kind: edit.kind, data: {...edit.data, text: label.value}});
        } else {
            showStatus();
        }
        return true;
    }

    function deleteSelection() {
        const item = items.get(selected);
        if (!item || item.deleted || !canMutate() || editing || gesture) return false;
        change({id: item.id, revision: item.revision, delete: true},
            {captureImage: item.kind === "image" ? item.id : null});
        return true;
    }

    // Replays get a new server revision, but can still lead to the exact state
    // expected by an older local action. A remote revision never inherits it.
    function historyVersion(item, versions = replayVersions) {
        if (!item) return 0;
        const link = versions.get(item.id);
        return link?.revision === item.revision ? link.version : item.revision;
    }

    function remember(changes, job) {
        if (!changes?.length) return;
        const entries = changes.map(({before, after}) => ({before, after,
            previous: historyVersion(before, job.versions)}));
        undo.push({entries, image: job.image});
        if (undo.length > historyLimit) undo.shift();
        redo.length = 0;
    }

    function historyApplied(job, elements) {
        const {frame, backwards} = job.history;
        (backwards ? undo : redo).pop();
        (backwards ? redo : undo).push(frame);
        if (job.image) frame.image = job.image;
        for (const {after, previous} of frame.entries) {
            const result = elements.get(after.id);
            replayVersions.set(after.id, {revision: result.revision, version: backwards ? previous : after.revision});
        }
    }

    function replay(backwards) {
        if (!hasHistory || !canMutate() || editing || gesture) return;
        const stack = backwards ? undo : redo;
        const frame = stack.at(-1);
        if (!frame) return;
        const changes = [];
        let captureImage = null;
        let image = null;
        for (const {before, after, previous} of frame.entries) {
            const current = items.get(after.id);
            if (!current || historyVersion(current) !== (backwards ? after.revision : previous)) {
                stack.pop();
                writeNotice = ["Правка пропущена: элемент уже изменился у другого участника.", true];
                showStatus();
                return;
            }
            const desired = backwards ? before : after;
            const base = {id: current.id, revision: current.revision};
            if (!desired || desired.deleted) {
                changes.push({...base, delete: true});
                if (current.kind === "image" && !frame.image) captureImage = current.id;
            } else {
                changes.push({...base, kind: desired.kind, data: desired.data});
                if (current.kind === "image" && current.deleted) image = frame.image;
            }
        }
        const json = JSON.stringify(changes);
        const body = image ? new FormData() : json;
        if (image) {
            body.append("changes", json);
            body.append("image", image);
        }
        resetArrow();
        updateHint();
        paintPreview();
        canvas.focus({preventScroll: true});
        send({url: board.dataset.historyUrl, body, headers: image ? {} : {"Content-Type": "application/json"},
            captureImage, history: {frame, backwards}});
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
        if (hasHistory) {
            undoButton.disabled = !canMutate() || Boolean(editing) || !undo.length;
            redoButton.disabled = !canMutate() || Boolean(editing) || !redo.length;
        }
        const protect = !closed && (busy || Boolean(failedWrite) || Boolean(labelChanged()));
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
        editing = null;
        arrowStart = null;
        arrowTracking = null;
        items.clear();
        nodes.clear();
        arrows.clear();
        undo.length = 0;
        redo.length = 0;
        replayVersions.clear();
        scene.replaceChildren();
        connections.replaceChildren();
        controls.hidden = true;
        find("view").hidden = true;
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
            if (arrowStart?.id && !visibleItem(arrowStart.id)) resetArrow();
            updateHint();
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

    async function send(job, retrying = false) {
        if (!canEdit || cursor === null || closed || busy || failedWrite && !retrying) return;
        busy = true;
        failedWrite = null;
        writeNotice = null;
        job.versions ??= new Map(replayVersions);
        showStatus();
        let editAfter = null;
        try {
            if (job.captureImage && !job.image) {
                const snapshot = await fetch(board.dataset.imageBase + job.captureImage + "/", {
                    cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(10000),
                });
                if (closed) return;
                if (snapshot.status === 404) {
                    writeNotice = ["Изображение уже недоступно.", true];
                    return;
                }
                if (!snapshot.ok) throw new Error("image");
                job.image = await snapshot.blob();
            }
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
            if (response.ok && job.history && data?.elements) {
                const elements = new Map(data.elements.map(item => [item.id, item]));
                if (!job.history.frame.entries.every(entry => elements.has(entry.after.id))) throw new Error("history");
                elements.forEach(merge);
                historyApplied(job, elements);
                updateProperties();
            } else if (response.ok && data?.element) {
                if (data.changes) {
                    data.changes.forEach(({after}) => merge(after));
                    remember(data.changes, job);
                } else {
                    merge(data.element);
                }
                if (job.select && !items.get(data.element.id)?.deleted) {
                    selected = data.element.id;
                    setTool("select");
                }
                updateProperties();
                if (job.focusLabel && selected === data.element.id) editAfter = selected;
            } else if (response.status >= 400 && response.status < 500) {
                if (data?.element) merge(data.element);
                if (job.history && response.status === 409) (job.history.backwards ? undo : redo).pop();
                updateProperties();
                writeNotice = [data?.error || "Правка не принята. Проверьте данные.", true];
            } else {
                throw new Error("write");
            }
        } catch {
            if (!closed) failedWrite = job;
        } finally {
            busy = false;
            if (editAfter) beginLabel(editAfter);
            if (!closed) updateHint();
            paintPreview();
            showStatus();
        }
    }

    function change(payload, options = {}) {
        return send({url: board.dataset.changeUrl, body: JSON.stringify(payload),
            headers: {"Content-Type": "application/json"}, ...options});
    }

    function updateHint() {
        const text = editing ? "Enter — сохранить, Shift+Enter — новая строка, Esc — отменить. Размер меняется ручками по краям."
            : arrowStart ? "Укажите конец стрелки. Esc — отменить."
            : tool === "select" && items.get(selected)?.kind === "arrow"
                ? "Потяните за конец стрелки, чтобы привязать его к фигуре или оставить свободным."
                : hints[tool];
        if (hint.textContent !== text) hint.textContent = text;
    }

    function resetArrow() {
        arrowStart = null;
        arrowTracking = null;
    }

    function setTool(next) {
        if (editing && !finishLabel()) return;
        tool = next;
        canvas.dataset.tool = next;
        resetArrow();
        updateHint();
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
        selected = id || null;
        updateProperties();
        updateHint();
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

    function resizeShape(g, p) {
        const d = g.source.data;
        const dx = p.x - g.start.x;
        const dy = p.y - g.start.y;
        let w = g.side.includes("e") ? size(d.w + dx) : d.w;
        let h = g.side.includes("s") ? size(d.h + dy) : d.h;
        if (["circle", "square"].includes(g.source.kind)) {
            const useWidth = g.side === "e" || (g.side === "se" && Math.abs(dx) >= Math.abs(dy));
            w = h = useWidth ? w : h;
        }
        return {...g.source, data: {...d, w, h}};
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
            const x = coord(d.x + p.x - g.start.x), y = coord(d.y + p.y - g.start.y);
            g.draft = x === d.x && y === d.y ? null : {...g.source, data: {...d, x, y}};
        } else if (g.mode === "resize") {
            const resized = resizeShape(g, p);
            g.draft = (resized.data.w !== g.source.data.w || resized.data.h !== g.source.data.h) ? resized : null;
        } else if (g.mode === "arrow") {
            g.to = snap(p, g.from.id);
            g.dragged ||= Math.hypot(p.x - g.start.x, p.y - g.start.y) >= 6 / zoom;
        } else if (g.mode === "arrow-end") {
            if (!g.draft && Math.hypot(p.x - g.start.x, p.y - g.start.y) < 2 / zoom) return;
            const d = g.source.data;
            const endpoint = snap(p, d[g.side === "source" ? "target" : "source"]);
            const field = g.side === "source" ? "start" : "end";
            g.snap = endpoint.id;
            const same = endpoint.id === d[g.side] && (endpoint.id ||
                (p.x === d[field][0] && p.y === d[field][1]));
            const data = withArrowEnd(d, g.side, endpoint);
            const geometry = arrowGeometry(data);
            g.draft = !same && geometry && Math.hypot(geometry.end.x - geometry.start.x,
                geometry.end.y - geometry.start.y) >= 0.1 ? {...g.source, data} : null;
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
        if (!event.isPrimary || event.button !== 0 || gesture || closed || event.target.closest("[data-board-inline-text]")) return;
        const p = point(event);
        if (tool === "hand") {
            gesture = {mode: "hand", left, top, screenX: event.clientX, screenY: event.clientY};
        } else if (canMutate()) {
            if (tool === "select") {
                const side = event.target.dataset.boardResize;
                const arrowEnd = event.target.dataset.boardArrowEnd;
                if (arrowEnd && visibleItem(selected)?.kind === "arrow") {
                    canvas.focus({preventScroll: true});
                    gesture = {mode: "arrow-end", source: visibleItem(selected), start: p, side: arrowEnd};
                } else if (side && isShape(visibleItem(selected))) {
                    gesture = {mode: "resize", source: visibleItem(selected), start: p, side};
                } else {
                    if (editing) {
                        const target = event.target.closest("[data-element-id]")?.dataset.elementId ?? shapeAt(p)?.id;
                        if (finishLabel()) select(target);
                        return;
                    }
                    canvas.focus({preventScroll: true});
                    select(event.target.closest("[data-element-id]")?.dataset.elementId);
                    const item = visibleItem(selected);
                    if (!item || item.kind === "arrow") return;
                    gesture = {mode: "move", source: item, start: p};
                }
            } else if (tool === "arrow") {
                canvas.focus({preventScroll: true});
                const from = arrowStart || snap(p);
                gesture = {mode: "arrow", start: p, from, to: snap(p, from.id), color: color.value};
            } else {
                canvas.focus({preventScroll: true});
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
    canvas.addEventListener("pointermove", (event) => {
        if (gesture) {
            updateGesture(event);
        } else if (tool === "arrow" && canMutate()) {
            const p = point(event);
            arrowTracking = snap(p, arrowStart?.id);
            paintPreview();
        }
    });
    canvas.addEventListener("pointerleave", () => {
        if (tool !== "arrow" || gesture) return;
        arrowTracking = null;
        paintPreview();
    });
    canvas.addEventListener("pointerup", (event) => {
        if (!gesture || gesture.pointer !== event.pointerId) return;
        updateGesture(event);
        const g = gesture;
        const {draft, mode, source} = g;
        gesture = null;
        if (mode === "arrow") {
            const data = withArrowEnd(withArrowEnd({color: g.color}, "source", g.from), "target", g.to);
            const geometry = arrowGeometry(data);
            if ((g.dragged || arrowStart) && geometry && Math.hypot(geometry.end.x - geometry.start.x,
                geometry.end.y - geometry.start.y) >= 0.1 && (g.to.id ||
                Math.hypot(g.to.p.x - g.from.p.x, g.to.p.y - g.from.p.y) >= 6 / zoom)) {
                resetArrow();
                change({id: uuid(), revision: 0, kind: "arrow", data}, {select: true});
            } else if (!g.from.id || visibleItem(g.from.id)) {
                arrowStart = g.from;
                arrowTracking = null;
            }
            updateHint();
        } else if (draft && mode === "resize" && editing?.id === source.id) {
            editing.data = draft.data;
            label.focus({preventScroll: true});
            showStatus();
        } else if (draft) {
            if (draft.kind === "stroke" && draft.data.points.length === 1) draft.data.points.push([0.1, 0]);
            const newShape = !source && isShape(draft);
            change({id: source?.id || uuid(), revision: source?.revision || 0, kind: draft.kind, data: draft.data},
                {select: newShape, focusLabel: newShape});
        }
        paintPreview();
    });
    for (const type of ["pointercancel", "lostpointercapture"]) {
        canvas.addEventListener(type, (event) => {
            if (gesture?.pointer === event.pointerId) {
                gesture = null;
                paintPreview();
            }
        });
    }
    canvas.addEventListener("dblclick", (event) => {
        if (tool !== "select" || event.target.closest("[data-board-inline-text]")) return;
        const item = items.get(event.target.closest("[data-element-id]")?.dataset.elementId) || shapeAt(point(event));
        if (isShape(item)) beginLabel(item.id);
    });
    canvas.addEventListener("wheel", (event) => {
        if (gesture || closed || !event.ctrlKey && !event.metaKey) return;
        event.preventDefault();
        const rect = canvas.getBoundingClientRect();
        zoomAt(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX - rect.left, event.clientY - rect.top);
    }, {passive: false});
    board.querySelectorAll("[data-board-tool]").forEach((button) =>
        button.addEventListener("click", () => { if (!gesture) setTool(button.dataset.boardTool); }));
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

    const fullscreen = find("fullscreen");
    const fullscreenLabel = fullscreen.querySelector(".board-tool-label");
    const background = [];
    function showFullscreenState() {
        const expanded = document.fullscreenElement === board || board.classList.contains("board-editor--expanded");
        const text = expanded ? "Свернуть доску" : "На весь экран";
        fullscreen.setAttribute("aria-pressed", String(expanded));
        fullscreen.title = text;
        fullscreenLabel.textContent = text;
        placeView();
    }

    function expandInWindow(expanded) {
        board.classList.toggle("board-editor--expanded", expanded);
        document.body.classList.toggle("board-expanded", expanded);
        if (expanded) {
            for (let node = board; node !== document.body; node = node.parentElement) {
                for (const sibling of node.parentElement.children) {
                    if (sibling !== node && sibling instanceof HTMLElement && !sibling.inert) {
                        sibling.inert = true;
                        background.push(sibling);
                    }
                }
            }
        } else {
            while (background.length) background.pop().inert = false;
        }
        showFullscreenState();
        fullscreen.focus({preventScroll: true});
    }

    fullscreen.addEventListener("click", async () => {
        if (gesture) return;
        if (document.fullscreenElement === board) {
            await document.exitFullscreen();
        } else if (board.classList.contains("board-editor--expanded")) {
            expandInWindow(false);
        } else {
            try {
                await board.requestFullscreen();
            } catch {
                expandInWindow(true);
            }
        }
    });
    document.addEventListener("fullscreenchange", showFullscreenState);
    document.addEventListener("keydown", (event) => {
        if (!event.defaultPrevented && event.key === "Escape" && board.classList.contains("board-editor--expanded")) {
            event.preventDefault();
            expandInWindow(false);
        }
    });

    if (canEdit) {
        undoButton?.addEventListener("click", () => replay(true));
        redoButton?.addEventListener("click", () => replay(false));
        find("edit-label").addEventListener("click", () => beginLabel(selected));
        find("save-label").addEventListener("click", () => finishLabel());
        find("delete").addEventListener("click", () => {
            if (deleteSelection()) canvas.focus({preventScroll: true});
        });
        label.addEventListener("input", showStatus);
        board.addEventListener("keydown", (event) => {
            if (event.isComposing) return;
            if (event.key === "Escape") {
                if (editing && finishLabel(true)) event.preventDefault();
                else if (arrowStart || ["arrow", "arrow-end"].includes(gesture?.mode)) {
                    gesture = null;
                    resetArrow();
                    updateHint();
                    paintPreview();
                    event.preventDefault();
                }
                return;
            }
            const input = event.target.closest("input, select, textarea, [contenteditable]");
            if (hasHistory && !input && (event.ctrlKey || event.metaKey) && !event.altKey &&
                (event.code === "KeyZ" || event.key.toLowerCase() === "z")) {
                event.preventDefault();
                if (!event.repeat) replay(!event.shiftKey);
                return;
            }
            if (event.ctrlKey || event.metaKey || event.altKey) return;
            if (event.target === label) {
                if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    finishLabel();
                }
                return;
            }
            if (input) return;
            if (event.key === "Delete" && !event.repeat && deleteSelection()) event.preventDefault();
            if (event.key === "F2" && isShape(items.get(selected))) {
                event.preventDefault();
                beginLabel(selected);
            }
        });
        const file = find("file");
        find("upload").addEventListener("click", () => {
            if (finishLabel() && canMutate()) file.click();
        });
        file.addEventListener("change", () => {
            const image = file.files[0];
            file.value = "";
            if (!image || !canMutate() || editing) return;
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

    retry.addEventListener("click", () => { if (failedWrite) send(failedWrite, true); });
    document.addEventListener("visibilitychange", () => {
        clearTimeout(pollTimer);
        if (!document.hidden) poll();
    });
    new ResizeObserver(placeView).observe(canvas);
    setTool(tool);
    placeView();
    poll();
})();
