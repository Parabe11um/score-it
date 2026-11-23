const assert = require('node:assert/strict');
const {expect} = require('@playwright/test');

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAACAAAAAYCAIAAAAUMWhjAAAAJUlEQVR4nGNkaGCgKWCirfGjFoxaMGrBqAWjFoxaMGrBqAXUAgBuowCwfFg98AAAAABJRU5ErkJggg==', 'base64');
const canvas = (page) => page.locator('[data-board-canvas]');
const element = (page, kind) => page.locator('[data-element-kind="' + kind + '"]');
const status = (page) => page.locator('[data-board-status]');
const button = (page, name) => page.getByRole('button', {name, exact: true});
const caption = (page) => page.getByRole('textbox', {name: 'Подпись на фигуре или стикере'});

async function midpoint(locator) {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    assert(box, 'Expected a visible element on the board');
    return {x: box.x + box.width / 2, y: box.y + box.height / 2};
}

async function drag(page, from, to) {
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, {steps: 8});
    await page.mouse.up();
}

function arrowEnd(page, name) {
    return element(page, 'arrow').locator('line').nth(1).getAttribute(name).then(Number);
}

async function fresh(page) {
    await page.bringToFront();
    await expect(status(page)).toHaveText('Доска обновлена');
}

async function issue(owner, permission) {
    const share = owner.locator('details.board-share');
    if (await share.getAttribute('open') === null) await share.locator('summary').click();
    await owner.getByLabel('Права по ссылке').selectOption(permission);
    await button(owner, 'Создать ссылку').click();
    const url = await owner.locator('[data-board-share-url]').inputValue();
    assert(/#[A-Za-z0-9_-]{43}$/.test(url), 'Full URL must contain an invitation fragment');
    return url;
}

async function open(page, url) {
    const path = new URL(url).pathname;
    const request = page.waitForRequest(r => r.method() === 'POST' && new URL(r.url()).pathname === path);
    await page.goto(url, {waitUntil: 'commit'});
    assert.equal((await (await request).allHeaders()).origin, new URL(url).origin);
    await expect(page).toHaveURL(new URL(path, url).href);
    await fresh(page);
}

async function newBoard(base, project, owner, name) {
    await owner.bringToFront();
    await owner.goto(base + '/projects/' + project + '/overview/');
    await owner.getByRole('navigation', {name: 'Разделы проекта'}).getByRole('link', {name: 'Доски', exact: true}).click();
    await owner.getByLabel('Название доски').fill(name);
    await button(owner, 'Создать доску').click();
    await expect(owner.getByRole('heading', {name})).toBeVisible();
}

async function checkBoards({base, project, owner, first, second, reader}) {
    await newBoard(base, project, owner, 'Схема команды');
    const editUrl = await issue(owner, 'edit');
    await open(first, editUrl);
    await open(second, editUrl);
    await expect(first.locator('.workspace-nav')).toHaveCount(0);

    await first.bringToFront();
    const leftPanel = first.getByRole('complementary', {name: 'Инструменты доски'});
    const [toolBox, stageBox] = await Promise.all([leftPanel.boundingBox(), canvas(first).boundingBox()]);
    assert(toolBox && stageBox && toolBox.x < stageBox.x);
    await button(first, 'Круг').click();
    await canvas(first).click({position: {x: 50, y: 45}});
    await expect(element(first, 'circle')).toHaveCount(1);
    await expect(caption(first)).toBeFocused();
    assert(await caption(first).evaluate(node => !!node.closest('svg')));
    await caption(first).fill('XНачало');
    // Home starts the current visual line, which depends on font metrics and wrapping.
    await caption(first).evaluate(node => node.setSelectionRange(0, 0));
    await first.keyboard.press('Delete');
    await expect(caption(first)).toHaveValue('Начало');
    await expect(element(first, 'circle')).toHaveCount(1);
    await caption(first).press('Enter');
    await expect(element(first, 'circle').locator('tspan')).toContainText('Начало');
    await element(first, 'circle').locator('circle').dblclick();
    await expect(caption(first)).toHaveValue('Начало');
    await caption(first).fill('Не сохранять');
    await caption(first).press('Escape');
    await expect(caption(first)).toBeHidden();
    await expect(element(first, 'circle').locator('tspan')).toContainText('Начало');

    await second.bringToFront();
    await expect(element(second, 'circle').locator('tspan')).toContainText('Начало');
    await button(second, 'Стикер').click();
    await canvas(second).click({position: {x: 280, y: 65}});
    await expect(caption(second)).toBeFocused();
    await caption(second).fill('Нужен ответ');
    const corner = await midpoint(second.locator('[data-board-resize="se"]'));
    await drag(second, corner, {x: corner.x + 56, y: corner.y + 42});
    await expect(caption(second)).toBeFocused();
    await expect(caption(second)).toHaveValue('Нужен ответ');
    await button(second, 'Готово').click();
    await expect(element(second, 'note').locator('tspan')).toContainText('Нужен ответ');
    await expect(element(second, 'note').locator('rect')).toHaveAttribute('width', '226');
    await expect(element(second, 'note').locator('rect')).toHaveAttribute('height', '150');

    await first.bringToFront();
    await expect(element(first, 'note').locator('tspan')).toContainText('Нужен ответ');
    await expect(element(first, 'note').locator('rect')).toHaveAttribute('width', '226');
    const startingHeight = await canvas(first).evaluate(node => node.clientHeight);
    await button(first, 'На весь экран').click();
    await expect(button(first, 'Свернуть доску')).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(() => canvas(first).evaluate(node => node.clientHeight)).toBeGreaterThan(startingHeight);
    await expect(element(first, 'note').locator('tspan')).toContainText('Нужен ответ');
    await button(first, 'Свернуть доску').click();
    await expect(button(first, 'На весь экран')).toHaveAttribute('aria-pressed', 'false');
    await expect.poll(() => canvas(first).evaluate(node => node.clientHeight)).toBe(startingHeight);

    await button(first, 'Стрелка').click();
    await element(first, 'circle').locator('circle').click();
    await expect(first.locator('[data-board-snap="source"]')).toHaveCount(1);
    await element(first, 'note').locator('rect').click();
    await expect(element(first, 'arrow')).toHaveCount(1);
    assert(Math.abs(await arrowEnd(first, 'x2') - 280) < 1, 'Arrow attaches to the edge of the note');
    const oldStart = await arrowEnd(first, 'x1');
    const oldEnd = await arrowEnd(first, 'x2');
    await element(first, 'circle').locator('circle').click();
    const circleGrip = await midpoint(first.locator('[data-board-resize="e"]'));
    await drag(first, circleGrip, {x: circleGrip.x + 30, y: circleGrip.y});
    await expect(element(first, 'circle').locator('circle')).toHaveAttribute('r', '71');
    await expect.poll(() => arrowEnd(first, 'x1')).toBeGreaterThan(oldStart);
    const noteCenter = await midpoint(element(first, 'note').locator('rect'));
    await drag(first, noteCenter, {x: noteCenter.x + 33, y: noteCenter.y + 25});
    await expect.poll(() => arrowEnd(first, 'x2')).toBeGreaterThan(oldEnd + 25);
    await fresh(first);
    await second.bringToFront();
    await expect.poll(() => arrowEnd(second, 'x2')).toBeGreaterThan(oldEnd + 25);

    await first.bringToFront();
    await button(first, 'Стрелка').click();
    const from = await midpoint(element(first, 'note').locator('rect'));
    const to = await midpoint(element(first, 'circle').locator('circle'));
    await drag(first, from, {x: to.x + 76, y: to.y});
    await expect(element(first, 'arrow')).toHaveCount(2);
    await first.keyboard.press('Delete');
    await expect(element(first, 'arrow')).toHaveCount(1);

    await button(first, 'Карандаш').click();
    await canvas(first).scrollIntoViewIfNeeded();
    const position = await canvas(first).boundingBox();
    await first.mouse.move(position.x + 75, position.y + 220);
    await first.mouse.down();
    await first.mouse.move(position.x + 185, position.y + 250, {steps: 8});
    await first.mouse.up();
    await expect(element(first, 'stroke')).toHaveCount(1);

    await first.locator('[data-board-file]').setInputFiles({name: 'board.png', mimeType: 'image/png', buffer: png});
    await expect(element(first, 'image').locator('image')).toHaveCount(1);
    await canvas(first).focus();
    await first.keyboard.press('Control+z');
    await expect(element(first, 'image')).toHaveCount(0);
    await button(first, 'Повторить правку').click();
    await expect(element(first, 'image').locator('image')).toHaveCount(1);
    await button(first, 'Увеличить масштаб').click();
    await expect(first.locator('[data-board-zoom="reset"]')).toHaveText('125%');
    await button(first, 'Вся доска').click();
    await expect.poll(async () => Number.parseInt(await first.locator('[data-board-zoom="reset"]').textContent(), 10))
        .toBeLessThanOrEqual(100);

    await second.bringToFront();
    await second.reload();
    await expect(element(second, 'circle')).toHaveCount(1);
    await expect(element(second, 'note')).toHaveCount(1);
    await expect(element(second, 'arrow')).toHaveCount(1);
    await expect(element(second, 'stroke')).toHaveCount(1);
    const image = element(second, 'image').locator('image');
    await expect(image).toHaveCount(1);
    const imageUrl = await image.getAttribute('href');
    assert.equal((await second.context().request.get(base + imageUrl)).status(), 200);

    const readUrl = await issue(owner, 'view');
    await open(reader, readUrl);
    await expect(element(reader, 'note')).toHaveCount(1);
    await expect(element(reader, 'arrow')).toHaveCount(1);
    await expect(button(reader, 'Стикер')).toHaveCount(0);
    await expect(button(reader, 'Стрелка')).toHaveCount(0);
    const viewer = new URL(readUrl).pathname;
    const cookies = await reader.context().cookies(base);
    const csrf = cookies.find(cookie => cookie.name === 'csrftoken');
    assert(csrf);
    const denied = await reader.context().request.post(base + viewer + 'change/', {
        headers: {Origin: base, 'X-CSRFToken': csrf.value}, data: {},
    });
    assert.equal(denied.status(), 403);

    await first.bringToFront();
    await button(first, 'Вернуть исходный вид').click();
    await element(first, 'circle').locator('circle').click();
    await first.keyboard.press('Delete');
    await expect(element(first, 'circle')).toHaveCount(0);
    await reader.bringToFront();
    await expect(element(reader, 'arrow')).toHaveCount(0);
    await expect(element(reader, 'note')).toHaveCount(1);

    await owner.bringToFront();
    await owner.locator('.board-share__links li').filter({hasText: 'Редактирование'}).getByRole('button', {name: 'Отозвать'}).click();
    await first.bringToFront();
    await expect(status(first)).toHaveText('Доступ к доске закрыт.');
    await expect(element(first, 'circle')).toHaveCount(0);
    await reader.bringToFront();
    assert.equal((await reader.context().request.get(base + viewer + 'state/')).status(), 200);
}

async function checkBoardHistory({base, project, owner, first, second}) {
    const arrow = element(first, 'arrow');
    const rectangle = element(first, 'rectangle');
    const targetGrip = first.locator('[data-board-arrow-end="target"]');
    const undo = button(first, 'Отменить правку');
    const redo = button(first, 'Повторить правку');
    await newBoard(base, project, owner, 'История схемы');
    const url = await issue(owner, 'edit');
    await open(first, url);
    await expect(undo).toBeDisabled();
    await button(first, 'Прямоугольник').click();
    await canvas(first).click({position: {x: 42, y: 80}});
    await expect(caption(first)).toBeFocused();
    await first.keyboard.insertText('А');
    await first.keyboard.press('Control+z');
    await expect(caption(first)).toHaveValue('');
    await expect(rectangle).toHaveCount(1);
    await caption(first).fill('Начало');
    await caption(first).press('Enter');
    await expect(rectangle.locator('tspan')).toContainText('Начало');
    await first.keyboard.press('Control+z');
    await expect(rectangle.locator('tspan')).toHaveCount(0);
    await first.keyboard.press('Control+Shift+z');
    await expect(rectangle.locator('tspan')).toContainText('Начало');

    await button(first, 'Стрелка').click();
    await canvas(first).scrollIntoViewIfNeeded();
    const stage = await canvas(first).boundingBox();
    assert(stage && stage.width > 540, 'Expected space to draw and attach an arrow');
    await drag(first, await midpoint(rectangle.locator('rect')), {x: stage.x + 350, y: stage.y + 135});
    await expect(arrow).toHaveCount(1);
    assert.equal(await arrowEnd(first, 'x1'), 212);
    assert.equal(await arrowEnd(first, 'x2'), 350);
    await button(first, 'Квадрат').click();
    await canvas(first).click({position: {x: 420, y: 78}});
    await expect(caption(first)).toBeFocused();
    await caption(first).press('Escape');
    await arrow.locator('line').nth(1).click();
    await drag(first, await midpoint(targetGrip), await midpoint(element(first, 'square').locator('rect')));
    await expect.poll(() => arrowEnd(first, 'x2')).toBe(420);
    await expect(undo).toBeEnabled();
    await undo.click();
    await expect.poll(() => arrowEnd(first, 'x2')).toBe(350);
    await expect(redo).toBeEnabled();
    await first.keyboard.press('Control+Shift+z');
    await expect.poll(() => arrowEnd(first, 'x2')).toBe(420);

    await open(second, url);
    await expect.poll(() => arrowEnd(second, 'x2')).toBe(420);
    await first.bringToFront();
    await rectangle.locator('rect').click();
    await first.keyboard.press('Delete');
    await expect(rectangle).toHaveCount(0);
    await expect(arrow).toHaveCount(0);
    await first.keyboard.press('Control+z');
    await expect(rectangle).toHaveCount(1);
    await expect.poll(() => arrowEnd(first, 'x2')).toBe(420);
    await first.keyboard.press('Control+z');
    await expect.poll(() => arrowEnd(first, 'x2')).toBe(350);
    await first.keyboard.press('Control+Shift+z');
    await expect.poll(() => arrowEnd(first, 'x2')).toBe(420);

    // A new edit clears the unused redo branch (the deletion above).
    const start = await midpoint(rectangle.locator('rect'));
    await drag(first, start, {x: start.x, y: start.y + 20});
    await expect(rectangle).toHaveAttribute('transform', 'translate(42 100)');
    await expect(redo).toBeDisabled();
    await second.bringToFront();
    const peerBlock = element(second, 'rectangle');
    await expect(peerBlock).toHaveAttribute('transform', 'translate(42 100)');
    const peerStart = await midpoint(peerBlock.locator('rect'));
    await drag(second, peerStart, {x: peerStart.x, y: peerStart.y + 25});
    await expect(peerBlock).toHaveAttribute('transform', 'translate(42 125)');
    await first.bringToFront();
    await expect(rectangle).toHaveAttribute('transform', 'translate(42 125)');
    await first.keyboard.press('Control+z');
    await expect(status(first)).toContainText('элемент уже изменился');
    await expect(rectangle).toHaveAttribute('transform', 'translate(42 125)');

    await button(first, 'Стрелка').click();
    await canvas(first).click({position: {x: 60, y: 290}});
    await canvas(first).click({position: {x: 210, y: 290}});
    await expect(arrow).toHaveCount(2);
    await expect(arrow.last().locator('line').nth(1)).toHaveAttribute('x2', '210');
    await first.keyboard.press('Control+z');
    await expect(arrow).toHaveCount(1);
}

async function checkBoardGroupTools({base, project, owner, first, second}) {
    await newBoard(base, project, owner, 'Схема с группами');
    const url = await issue(owner, 'edit');
    await open(first, url);
    const board = canvas(first);
    const circle = element(first, 'circle');
    const diamond = element(first, 'diamond');
    const ellipse = element(first, 'ellipse');
    const arrow = element(first, 'arrow');
    const ports = first.locator('[data-board-connect]');
    const frame = first.locator('[data-board-selection]');
    const more = first.locator('.board-more-shapes summary');

    await button(first, 'Круг').click();
    await board.click({position: {x: 54, y: 80}});
    await expect(caption(first)).toBeFocused();
    await caption(first).fill('Два');
    await first.keyboard.press('Space');
    await expect(caption(first)).toHaveValue('Два ');
    await expect(board).toHaveAttribute('data-tool', 'select');
    await first.keyboard.press('Escape');
    await more.click();
    await button(first, 'Ромб').click();
    await board.click({position: {x: 267, y: 80}});
    await expect(caption(first)).toBeFocused();
    await first.keyboard.press('Escape');
    await button(first, 'Овал').click();
    await board.click({position: {x: 520, y: 280}});
    await expect(caption(first)).toBeFocused();
    await first.keyboard.press('Escape');
    await more.click();
    await expect(diamond.locator('polygon')).toHaveCount(1);
    await expect(ellipse.locator('ellipse')).toHaveCount(1);

    await button(first, 'Карандаш').click();
    await board.scrollIntoViewIfNeeded();
    const stage = await board.boundingBox();
    assert(stage && stage.width > 700 && stage.height > 400);
    const at = (x, y) => ({x: stage.x + x, y: stage.y + y});
    await first.keyboard.down('Space');
    await expect(board).toHaveAttribute('data-tool', 'hand');
    await drag(first, at(130, 265), at(170, 295));
    await first.keyboard.up('Space');
    await expect(board).toHaveAttribute('data-tool', 'stroke');
    assert.match(await board.getAttribute('viewBox'), /^-40 -30 /);
    await expect(element(first, 'stroke')).toHaveCount(0);
    await button(first, 'Вернуть исходный вид').click();
    await button(first, 'Выбрать').click();

    await circle.locator('circle').click();
    await expect(ports).toHaveCount(4);
    await drag(first, await midpoint(first.locator('[data-board-connect="n"]')), await midpoint(diamond.locator('polygon')));
    await expect(arrow).toHaveCount(1);
    assert.equal(await arrowEnd(first, 'x1'), 110);
    assert.equal(await arrowEnd(first, 'y1'), 80);

    await drag(first, at(20, 45), at(452, 230));
    await expect(frame).toHaveCount(1);
    await expect(first.locator('[data-board-hint]')).toContainText('Фигур выделено: 2');
    const start = await midpoint(circle.locator('circle'));
    await drag(first, start, {x: start.x + 30, y: start.y + 35});
    await expect(circle).toHaveAttribute('transform', 'translate(84 115)');
    await expect(diamond).toHaveAttribute('transform', 'translate(297 115)');
    await expect(ellipse).toHaveAttribute('transform', 'translate(520 280)');
    assert.equal(await arrowEnd(first, 'x1'), 140);
    assert.equal(await arrowEnd(first, 'y1'), 115);

    const grip = await midpoint(first.locator('[data-board-resize="se"]'));
    await drag(first, grip, {x: grip.x + 76, y: grip.y + 22});
    await expect.poll(async () => Number(await circle.locator('circle').getAttribute('r'))).toBeGreaterThan(60);
    const stretched = await diamond.getAttribute('transform');
    assert.notEqual(stretched, 'translate(297 115)');
    await first.keyboard.press('Control+z');
    await expect(circle.locator('circle')).toHaveAttribute('r', '56');
    await expect(diamond).toHaveAttribute('transform', 'translate(297 115)');
    await first.keyboard.press('Control+Shift+z');
    await expect(diamond).toHaveAttribute('transform', stretched);

    await first.keyboard.press('Delete');
    await expect(circle).toHaveCount(0);
    await expect(diamond).toHaveCount(0);
    await expect(arrow).toHaveCount(0);
    await expect(ellipse).toHaveCount(1);
    await first.keyboard.press('Control+z');
    await expect(circle).toHaveCount(1);
    await expect(diamond).toHaveAttribute('transform', stretched);
    await expect(arrow).toHaveCount(1);

    await open(second, url);
    await expect(element(second, 'diamond')).toHaveAttribute('transform', stretched);
    await expect(element(second, 'ellipse')).toHaveAttribute('transform', 'translate(520 280)');
    assert.equal(await arrowEnd(second, 'y1'), 115);
}

module.exports = {checkBoards, checkBoardHistory, checkBoardGroupTools};
