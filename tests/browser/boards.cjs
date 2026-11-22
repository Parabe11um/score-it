const assert = require('node:assert/strict');
const {expect} = require('@playwright/test');

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAACAAAAAYCAIAAAAUMWhjAAAAJUlEQVR4nGNkaGCgKWCirfGjFoxaMGrBqAWjFoxaMGrBqAXUAgBuowCwfFg98AAAAABJRU5ErkJggg==', 'base64');
const canvas = (page) => page.locator('[data-board-canvas]');
const element = (page, kind) => page.locator('[data-element-kind="' + kind + '"]');
const status = (page) => page.locator('[data-board-status]');
const button = (page, name) => page.getByRole('button', {name, exact: true});

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

async function checkBoards({base, project, owner, first, second, reader}) {
    await owner.goto(base + '/projects/' + project + '/overview/');
    await owner.getByRole('navigation', {name: 'Разделы проекта'}).getByRole('link', {name: 'Доски', exact: true}).click();
    await owner.getByLabel('Название доски').fill('Схема команды');
    await button(owner, 'Создать доску').click();
    await expect(owner.getByRole('heading', {name: 'Схема команды'})).toBeVisible();
    const editUrl = await issue(owner, 'edit');
    await open(first, editUrl);
    await open(second, editUrl);
    await expect(first.locator('.workspace-nav')).toHaveCount(0);

    await first.bringToFront();
    await button(first, 'Круг').click();
    await canvas(first).click({position: {x: 50, y: 45}});
    await expect(element(first, 'circle')).toHaveCount(1);
    await first.getByLabel('Подпись на фигуре или стикере').fill('Начало');
    await button(first, 'Сохранить подпись').click();
    await expect(element(first, 'circle').locator('tspan')).toContainText('Начало');

    await second.bringToFront();
    await expect(element(second, 'circle').locator('tspan')).toContainText('Начало');
    await button(second, 'Стикер').click();
    await canvas(second).click({position: {x: 280, y: 65}});
    await expect(second.getByLabel('Подпись на фигуре или стикере')).toBeFocused();
    await second.getByLabel('Подпись на фигуре или стикере').fill('Нужен ответ');
    await button(second, 'Сохранить подпись').click();
    await expect(element(second, 'note').locator('tspan')).toContainText('Нужен ответ');

    await first.bringToFront();
    await expect(element(first, 'note').locator('tspan')).toContainText('Нужен ответ');
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
    await button(first, 'Увеличить масштаб').click();
    await expect(first.locator('[data-board-zoom="reset"]')).toHaveText('125%');
    await button(first, 'Вся доска').click();
    await expect(first.locator('[data-board-zoom="reset"]')).toHaveText('100%');

    await second.bringToFront();
    await second.reload();
    await expect(element(second, 'circle')).toHaveCount(1);
    await expect(element(second, 'note')).toHaveCount(1);
    await expect(element(second, 'stroke')).toHaveCount(1);
    const image = element(second, 'image').locator('image');
    await expect(image).toHaveCount(1);
    const imageUrl = await image.getAttribute('href');
    assert.equal((await second.context().request.get(base + imageUrl)).status(), 200);

    const readUrl = await issue(owner, 'view');
    await open(reader, readUrl);
    await expect(element(reader, 'note')).toHaveCount(1);
    await expect(button(reader, 'Стикер')).toHaveCount(0);
    const viewer = new URL(readUrl).pathname;
    const cookies = await reader.context().cookies(base);
    const csrf = cookies.find(cookie => cookie.name === 'csrftoken');
    assert(csrf);
    const denied = await reader.context().request.post(base + viewer + 'change/', {
        headers: {Origin: base, 'X-CSRFToken': csrf.value}, data: {},
    });
    assert.equal(denied.status(), 403);

    await owner.bringToFront();
    await owner.locator('.board-share__links li').filter({hasText: 'Редактирование'}).getByRole('button', {name: 'Отозвать'}).click();
    await first.bringToFront();
    await expect(status(first)).toHaveText('Доступ к доске закрыт.');
    await expect(element(first, 'circle')).toHaveCount(0);
    await reader.bringToFront();
    assert.equal((await reader.context().request.get(base + viewer + 'state/')).status(), 200);
}

module.exports = {checkBoards};
