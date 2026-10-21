// Run only against the private localhost Caddy created by check_browser_security.py.
// The fixture arrives over stdin; no credentials, cookies or resume URLs are logged.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium, expect} = require('@playwright/test');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const {fixture, base_url: base} = input;
assert.match(base, /^https:\/\/localhost:[0-9]+$/);
const completed = [];
let activeCheck = '';
let browser;

function safe(message) {
    let text = String(message);
    for (const user of fixture.users) text = text.split(user.password).join('[redacted]');
    return text.replace(/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/gi, '[redacted]')
        .replace(/(\/room\/)[^\s/'"?]+/g, '$1[redacted]')
        .replace(/\b(?:set-cookie|cookie|x-csrftoken):[^\n]+/gi, '[redacted header]')
        .replace(/\b(csrfmiddlewaretoken|csrftoken|sessionid)\s*[=:]\s*["']?[^\s;"']+/gi, '$1=[redacted]');
}

async function check(name, action) {
    activeCheck = name;
    await action();
    completed.push(name);
}

async function nativePost(page, path, action) {
    const observed = page.waitForRequest(request => request.method() === 'POST'
        && new URL(request.url()).pathname === path);
    await action();
    const request = await observed;
    assert.equal((await request.allHeaders()).origin, base, 'Native POST must send its real Origin');
}

async function login(page, user) {
    const response = await page.goto(base + '/login/');
    assert.equal(response.status(), 200);
    assert.equal(response.headers()['referrer-policy'], 'strict-origin');
    await page.locator('[name=username]').fill(user.username);
    await page.locator('[name=password]').fill(user.password);
    await nativePost(page, '/login/', () => page.locator('form').getByRole('button', {name: 'Войти', exact: true}).click());
    await expect(page).toHaveURL(base + '/');
}

async function probe(context, path, form) {
    if (form === undefined) return context.request.get(base + path, {maxRedirects: 0});
    const cookies = await context.cookies(base);
    const csrf = cookies.find(cookie => cookie.name === 'csrftoken');
    assert(csrf, 'Probe must use valid CSRF so authorization, rather than CSRF rejection, is tested');
    return context.request.post(base + path, {maxRedirects: 0,
        form: {...form, csrfmiddlewaretoken: csrf.value},
        headers: {Origin: base, Referer: base + '/'}});
}

async function state(page, publicUrl) {
    const response = await page.context().request.get(publicUrl + 'state/');
    assert.equal(response.status(), 200);
    return response.json();
}

async function main() {
    browser = await chromium.launch({headless: true});
    const contexts = [];
    async function actor() {
        // Only this temporary proxy uses a self-signed certificate. Public TLS
        // issuer/trust validation is a separate check; no production URL is used.
        const context = await browser.newContext({ignoreHTTPSErrors: true});
        contexts.push(context);
        const page = await context.newPage();
        page.setDefaultTimeout(15000);
        page.on('dialog', dialog => dialog.accept());
        return page;
    }
    try {
        const owner = await actor();
        const other = await actor();
        let first = await actor();
        const second = await actor();
        const anonymous = await actor();
        await check('native_https_login', async () => {
            await login(owner, fixture.users[0]);
            await login(other, fixture.users[1]);
            const cookies = await owner.context().cookies(base);
            for (const name of ['sessionid', 'csrftoken']) {
                const cookie = cookies.find(item => item.name === name);
                assert(cookie && cookie.secure && cookie.sameSite === 'Lax');
                if (name === 'sessionid') assert(cookie.httpOnly);
            }
        });
        await check('foreign_project_sprint_and_admin_rejected', async () => {
            for (const path of [`/projects/${fixture.foreign_project}/`,
                                `/sprints/${fixture.foreign_sprint}/`,
                                `/sessions/${fixture.foreign_room}/`,
                                `/sessions/${fixture.foreign_room}/export/xlsx/`]) {
                assert.equal((await probe(owner.context(), path)).status(), 404);
            }
            const response = await probe(owner.context(), '/admin/poker/organizerinvitation/');
            assert.equal(response.status(), 302);
            assert(response.headers().location.startsWith('/admin/login/'));
        });

        const importPath = `/projects/${fixture.project}/tasks/import/file/`;
        const importForm = owner.locator(`form[action="${importPath}"]`);
        await check('native_malformed_xlsx_returns_a_form_error', async () => {
            await owner.goto(base + `/projects/${fixture.project}/`);
            await importForm.locator('[name=task_file]').setInputFiles({name: 'synthetic.xlsx',
                mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('PK-bad')});
            const rejected = owner.waitForResponse(response => response.request().method() === 'POST'
                && new URL(response.url()).pathname === importPath);
            await nativePost(owner, importPath, () => importForm.getByRole('button', {name: 'Загрузить задачи из файла'}).click());
            assert.equal((await rejected).status(), 400);
            await expect(importForm.locator('.form-error')).toBeVisible();
        });
        await check('native_csv_import_succeeds_after_rejection', async () => {
            const headers = ['Идентификатор объекта', 'Код', 'Наименование', 'Текст',
                'Логический тип.Имя логического типа', 'Оценка задачи, час', 'Статус.Имя статуса'];
            const row = ['CmfTask:' + require('node:crypto').randomUUID(), 'BROWSER-IMPORT-1',
                'Synthetic imported task', '<p>Synthetic description</p>', 'Разработка АБС', '8', 'В работе'];
            const buffer = Buffer.from('\ufeff' + headers.join(';') + '\r\n' + row.join(';') + '\r\n');
            await importForm.locator('[name=task_file]').setInputFiles({name: 'synthetic.csv', mimeType: 'text/csv', buffer});
            await nativePost(owner, importPath, () => importForm.getByRole('button', {name: 'Загрузить задачи из файла'}).click());
            await expect(owner).toHaveURL(base + `/projects/${fixture.project}/`);
            await expect(owner.locator('body')).toContainText('BROWSER-IMPORT-1');
        });
        await check('oversized_upload_and_body_rejected_while_https_stays_healthy', async () => {
            const csrf = (await owner.context().cookies(base)).find(cookie => cookie.name === 'csrftoken').value;
            const headers = {Origin: base, Referer: base + '/'};
            const file = await owner.context().request.post(base + importPath, {headers, multipart: {
                csrfmiddlewaretoken: csrf, task_file: {name: 'synthetic.xlsx', mimeType: 'application/octet-stream',
                    buffer: Buffer.alloc(10 * 1024 * 1024 + 1, 120)}}});
            assert.equal(file.status(), 413);
            const body = await owner.context().request.post(base + '/login/', {headers: {...headers, 'X-CSRFToken': csrf},
                data: Buffer.alloc(11 * 1024 * 1024 + 1, 120)});
            assert.equal(body.status(), 413);
            assert.equal((await owner.context().request.get(base + '/healthz/')).status(), 200);
        });

        let publicUrl, roomId;
        await check('native_create_voting_room', async () => {
            await owner.goto(base + `/projects/${fixture.project}/`);
            const form = owner.locator(`form[action="/projects/${fixture.project}/sessions/new/"]`);
            await form.locator('xpath=..').locator('summary').click();
            await form.locator('[name=name]').fill(fixture.room_name);
            await form.locator('[name=minimum_participants]').fill('2');
            await form.locator(`[name=task_ids][value="${fixture.task}"]`).check();
            await nativePost(owner, `/projects/${fixture.project}/sessions/new/`,
                () => form.getByRole('button', {name: 'Создать комнату с очередью'}).click());
            await expect(owner).toHaveURL(/\/sessions\/[0-9]+\/$/);
            roomId = Number(new URL(owner.url()).pathname.split('/')[2]);
            publicUrl = await owner.locator('#public-url').inputValue();
            assert.equal(new URL(publicUrl).origin, base);
        });
        await check('foreign_room_reads_exports_and_mutations_rejected_with_valid_csrf', async () => {
            for (const suffix of ['', 'state/', 'export/csv/', 'export/xlsx/']) {
                assert.equal((await probe(other.context(), `/sessions/${roomId}/${suffix}`)).status(), 404);
            }
            for (const suffix of ['finish/', 'delete/', 'reveal/', 'accept/', 'queue/add/']) {
                assert.equal((await probe(other.context(), `/sessions/${roomId}/${suffix}`, {})).status(), 404);
            }
        });

        let oldResume;
        await check('native_join_two_separate_participants', async () => {
            for (const [page, name] of [[first, 'Browser first'], [second, 'Browser second']]) {
                await page.goto(publicUrl);
                await page.locator('[name=name]').fill(name);
                await nativePost(page, new URL(publicUrl).pathname + 'join/',
                    () => page.getByRole('button', {name: 'Присоединиться'}).click());
                await expect(page.locator('#room-app')).toBeVisible();
            }
            oldResume = await first.locator('#copy-resume-link').getAttribute('data-resume-url');
        });
        await check('anonymous_and_cross_room_participant_rejected', async () => {
            await anonymous.goto(publicUrl);
            assert.equal((await probe(anonymous.context(), new URL(publicUrl).pathname + 'state/')).status(), 403);
            assert.equal((await probe(anonymous.context(), new URL(publicUrl).pathname + 'vote/', {value: 52})).status(), 403);
            const foreign = `/room/${fixture.foreign_public_token}/`;
            assert.equal((await probe(first.context(), foreign + 'state/')).status(), 403);
            assert.equal((await probe(first.context(), foreign + 'vote/', {value: 52})).status(), 403);
        });
        await check('native_start_and_two_browser_votes', async () => {
            await nativePost(owner, `/sessions/${roomId}/start/`,
                () => owner.getByRole('button', {name: 'Начать оценку'}).click());
            for (const [page, value] of [[first, 2], [second, 8]]) {
                await expect(page.locator('#voting-state')).toBeVisible();
                await page.locator(`#vote-cards [data-value="${value}"]`).click();
                await expect(page.locator('#vote-feedback')).toContainText(`Ваш выбор: ${value} ч`);
                assert.equal((await state(page, publicUrl)).round.my_vote, value);
            }
        });
        await check('other_votes_and_tokens_hidden_until_reveal', async () => {
            for (const page of [first, second]) {
                const result = await state(page, publicUrl);
                assert.deepEqual(result.round.votes, []);
                assert.equal(result.round.average, null);
                assert.equal(result.round.final_estimate, null);
            }
            const response = await probe(owner.context(), `/sessions/${roomId}/state/`);
            assert.equal((await response.json()).summary, null);
        });
        await check('posted_participant_id_cannot_change_other_vote', async () => {
            const management = await (await probe(owner.context(), `/sessions/${roomId}/state/`)).json();
            const victim = management.participants.find(item => item.name === 'Browser second');
            const victimId = Number(victim.rotate_url.match(/participants\/([0-9]+)\//)[1]);
            const response = await probe(first.context(), new URL(publicUrl).pathname + 'vote/',
                {value: 2, participant_id: victimId, participant: victimId});
            assert.equal(response.status(), 200);
            assert.equal((await state(second, publicUrl)).round.my_vote, 8);
        });
        await check('rotation_revokes_existing_browser_and_old_resume_url', async () => {
            const management = await (await probe(owner.context(), `/sessions/${roomId}/state/`)).json();
            const original = management.participants.find(item => item.name === 'Browser first');
            const rotated = owner.waitForResponse(response => response.request().method() === 'POST'
                && response.url() === base + original.rotate_url);
            await owner.locator(`[data-resume-rotate="${original.rotate_url}"]`).click();
            const response = await rotated;
            assert.equal(response.status(), 200);
            const next = (await response.json()).resume_url;
            assert.notEqual(next, oldResume);
            assert.equal((await probe(first.context(), new URL(publicUrl).pathname + 'vote/', {value: 52})).status(), 403);
            const stale = await actor();
            await stale.goto(oldResume);
            await expect(stale.locator('[name=name]')).toBeVisible();
            const resumed = await actor();
            await resumed.goto(next);
            await expect(resumed.locator('#room-app')).toBeVisible();
            assert.equal((await state(resumed, publicUrl)).round.my_vote, 2);
            await first.close();
            first = resumed;
        });
        await check('native_reveal_and_accept_preserve_vote_totals', async () => {
            await expect(owner.locator('#reveal-button')).toBeEnabled();
            await nativePost(owner, `/sessions/${roomId}/reveal/`, () => owner.locator('#reveal-button').click());
            await expect(owner.locator('#final-estimate-value')).toHaveText('4');
            await expect(owner.locator('#average-value')).toHaveText('5');
            await expect(second.locator('#revealed-state')).toBeVisible();
            await expect(second.locator('#room-final-estimate')).toHaveText('4');
            await nativePost(owner, `/sessions/${roomId}/accept/`,
                () => owner.getByRole('button', {name: 'Сохранить оценку', exact: true}).click());
        });
        await check('accepted_estimates_export_only_to_owner', async () => {
            for (const extension of ['csv', 'xlsx']) {
                const response = await probe(owner.context(), `/sessions/${roomId}/export/${extension}/`);
                assert.equal(response.status(), 200);
                assert(response.headers()['content-disposition'].includes('attachment;'));
                if (extension === 'csv') assert((await response.text()).includes(fixture.task_number));
                else assert.equal((await response.body()).subarray(0, 2).toString(), 'PK');
                assert.equal((await probe(other.context(), `/sessions/${roomId}/export/${extension}/`)).status(), 404);
            }
        });
        await check('participants_complete_voting_through_ui', async () => {
            for (const page of [first, second]) {
                await expect(page.locator('#next-task')).toBeEnabled();
                await page.locator('#next-task').click();
                await expect(page.locator('#room-app')).toHaveCount(0);
                await expect(page.locator('h1')).toContainText('Спасибо');
            }
        });
        await check('relogin_rejects_stale_form_then_fresh_native_finish_succeeds', async () => {
            const oldToken = await owner.locator('form[action$="/finish/"] [name=csrfmiddlewaretoken]').inputValue();
            const loginTab = await owner.context().newPage();
            await login(loginTab, fixture.users[0]);
            const stale = await owner.context().request.post(base + `/sessions/${roomId}/finish/`, {
                form: {csrfmiddlewaretoken: oldToken}, headers: {Origin: base, Referer: base + '/'}, maxRedirects: 0});
            assert.equal(stale.status(), 403);
            await owner.reload();
            await nativePost(owner, `/sessions/${roomId}/finish/`,
                () => owner.getByRole('button', {name: 'Завершить сессию', exact: true}).click());
            await expect(owner.getByRole('heading', {name: 'Сессия завершена', exact: true})).toBeVisible();
        });
        await check('finished_room_rejects_late_vote_and_join', async () => {
            assert.equal((await probe(second.context(), new URL(publicUrl).pathname + 'vote/', {value: 52})).status(), 409);
            assert.equal((await probe(anonymous.context(), new URL(publicUrl).pathname + 'join/', {name: 'Late voter'})).status(), 302);
        });
        await check('native_logout_removes_organizer_access', async () => {
            await nativePost(owner, '/logout/', () => owner.getByRole('button', {name: 'Выйти', exact: true}).click());
            await expect(owner).toHaveURL(base + '/login/');
            const response = await probe(owner.context(), `/sessions/${roomId}/`);
            assert.equal(response.status(), 302);
            assert(response.headers().location.startsWith('/login/?next='));
        });
        console.log(JSON.stringify({ok: true, checks: completed, browser: browser.version(),
            playwright: require('@playwright/test/package.json').version,
            tls_scope: 'HTTPS on an isolated loopback proxy with a self-signed test certificate; issuer trust is not tested'}));
    } finally {
        for (const context of contexts) await context.close();
    }
}

main().catch(error => {
    console.log(JSON.stringify({ok: false, checks: completed, failed_check: activeCheck, error: safe(error.stack)}));
    process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
