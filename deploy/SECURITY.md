# Применение исправлений аудита от 20.10.2026

Изменения в Git сами по себе не обновляют работающие контейнеры. Работы выполняются
сначала на отдельном стенде, затем на рабочем сервере. Данные не переимпортируются.
Не удаляйте постоянный том PostgreSQL и не используйте `down -v`.

## 1. Обновить сборочный WSL

В отчёте Docker 24.0.2/runc 1.1.7 относятся к сборочному WSL. Для Ubuntu с уже
подключённым официальным репозиторием Docker:

```bash
sudo apt-get update
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker version
docker info
```

Если Docker работает через Docker Desktop, обновляется сам Docker Desktop, а не
пакеты Engine внутри WSL. Источник версии определяет раздел Server `docker version`.
Сборщик требует Docker Engine >=29.8.2; совместимость старых архивов с Docker 24
проверяется только отдельными изолированными CI-демонами. Рабочие серверы этими
командами не обновляются. Справка: https://docs.docker.com/engine/install/ubuntu/.

## 2. Собрать и проверить новый комплект

После слияния PR используйте чистую отдельную сборочную копию актуального main,
как при предыдущем выпуске. Не собирайте банковский комплект из незакоммиченных
правок. Запуск из корня репозитория:

```bash
bash deploy/make_release.sh /absolute/releases
```

В комплект входят приложение, PostgreSQL 17.11 и Caddy `v2.11.7-scoreit.1`.
Встроенные зависимости Caddy: Go 1.26.10, x/net 0.60.0. Версия 2.11.7 официального
образа сама по себе не покрывает все исправления x/net из аудита. Базовые образы
закреплены digest; исходный код Caddy — коммитом.

Финальный образ Caddy собирается из `scratch`: в него переносятся только новый
статический бинарный файл, CA-сертификаты, MIME/часовые данные, стандартный
Caddyfile, страница приветствия и файлы лицензий/пользователей. Alpine используется
только в промежуточном этапе для подготовки этих файлов. BusyBox, shell, curl,
nghttp2 и старый бинарный файл официального образа не попадают ни в один слой
итогового образа. Это проверяется по точному списку файлов всех слоёв сохранённого
архива; замена/удаление исполняемого файла поверх старого слоя проверку не обходит.
Пути `/data`, `/config`, `/etc/caddy/Caddyfile`, команда запуска и порты сохранены.
Диагностика использует `caddy version`/`caddy build-info`, без `sh`, `apk` и `curl`.

CI дополнительно проверяет HTTPS с синтетическим локальным УЦ, сохранение
сертификатов и конфигурации после перезапуска, отказ HTTPS upstream с недоверенным
сертификатом и успешное проксирование после добавления УЦ в стандартный CA bundle.
Проверки выполняются во внутренних тестовых сетях с отдельными временными томами.
Общие маршруты KidsTime/score-it и маскирование access/error logs проверяются
закреплённым тестом KidsTime из коммита `08dac907114be118b57ea281084f0b2bda88879e`.

Состав PostgreSQL не меняется на другую major-версию или другой дистрибутив.
Из runtime PostgreSQL удалён стандартный `ssl-cert-snakeoil` ключ Debian, не
используемый этой конфигурацией. Для TLS БД нужны отдельно выданные сертификаты.
Служебный `gosu` 1.19 также пересобран из закреплённого коммита с Go 1.26.10,
`moby/sys/user` 0.4.1 и `x/sys` 0.48.0. Это устраняет исправляемые находки старого
Go в helper, сохраняя штатный entrypoint и версию сервера PostgreSQL.

Сборщик создаёт `security/{app,postgres,caddy}.json`, SPDX SBOM каждого образа и
`summary.txt`. Сканируются точные сохранённые конфигурации. Текущая CVE-база
загружается на сборочном хосте; сервер в закрытом контуре ничего не скачивает.
Секреты и High/Critical в Go-модулях Caddy/gosu блокируют `RELEASE_READY`. Остальные
находки сохраняются для разбора, а не исключаются из отчёта. Сборка не обещает
нулевых CVE системных пакетов; часть находок исходного аудита не имела исправлений
для Bookworm. Перед банковским выпуском нужен повторный Scout/Trivy и решение по
каждой остающейся применимой находке. Успешная сборка не является допуском ИБ.

Подписать контрольные суммы ключом издателя, хранящимся отдельно:

```bash
bash deploy/sign_release.sh /absolute/score-it-RELEASE /private/release-signing-key.pem
```

На стороне банка открытый ключ получают доверенным отдельным каналом. Загрузка:

```bash
bash load_release.sh /absolute/trusted-release-public-key.pem
```

Без ключа допускается локальная проверка неподписанного тестового комплекта.
Подписанный комплект без доверенного ключа загрузчик отвергает.

## 3. Разделить роли существующей PostgreSQL

Перед началом сохраните в `.env.production` фактические `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD` и `DJANGO_SECRET_KEY` существующего стенда.
Смена этих значений не меняет уже созданных пользователей в томе. Не подставляйте
новое имя администратора из примера в существующую БД.

Добавьте `DB_USER=score_it_app`, `DB_MIGRATION_USER=score_it_migrate` и два новых
разных случайных пароля (`DB_PASSWORD`, `DB_MIGRATION_PASSWORD`). Сам `.env.production`
должен иметь права 0600; значения не отправляются в чат и не коммитятся.

После загрузки комплекта и замены его файлов в `/opt/score-it`, сохранив постоянный
`.env.production`, выполните из этого каталога:

```bash
COMPOSE=(docker compose --env-file .env.production --env-file release.env -f compose.production.yaml -f compose.maintenance.yaml)
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" up -d --pull never --no-build --wait db
"${COMPOSE[@]}" run --rm --no-deps --pull never db-admin python deploy/configure_db_roles.py
```

Это проверка без изменений. После её успешного завершения, в окно обслуживания:

```bash
"${COMPOSE[@]}" stop web
"${COMPOSE[@]}" run --rm --no-deps --pull never db-admin python deploy/configure_db_roles.py --apply
"${COMPOSE[@]}" run --rm --no-deps --pull never migrate
"${COMPOSE[@]}" run --rm --no-deps --pull never web python manage.py check_database_security
"${COMPOSE[@]}" -f compose.proxy.yaml up -d --pull never --no-build --wait web
"${COMPOSE[@]}" exec -T web python manage.py check_database_security
```

`DATABASE_SECURITY_OK` подтверждает отсутствие административных прав, владения
таблицами и прав CREATE/TEMP у runtime. Runtime получает SELECT/INSERT/UPDATE/DELETE
и использование последовательностей. Структурой владеет отдельная роль миграций.
Все существующие строки сохраняются; права на будущие таблицы задаются через
ALTER DEFAULT PRIVILEGES. Административная учётная запись остаётся только для
обслуживания отдельной БД; её пароль больше не находится в web-контейнере.

Скрипт работает в транзакции, повторное применение поддерживается. Он откажется
менять неожиданные схемы, объекты, владельцев, привилегированные выбранные роли
или роли с членством. При остановке выясните причину; не запускайте web со старой
административной ролью в качестве обхода проверки.

Если на стенде используется local-test override, сохраните его при запуске web
вместо proxy override. В дальнейшем `migrate` выполняется одноразовым сервисом
`migrate`, а не web. `transfer_data import` также выполняется сервисом `migrate`,
поскольку восстановление последовательностей требует владельца объектов.

## 4. Обновить общий Caddy и журналы

Caddy обслуживает также KidsTime. Он не входит в Compose проекта score-it;
его Compose и Caddyfile сначала нужно просмотреть на сервере. Не запускайте второй
прокси и не пересоздавайте контейнер отдельной командой `docker run`: потеряются
сетевые подключения, тома или маршруты.

`compose.caddy-image.yaml` — override для существующего сервиса с именем `caddy`.
Он меняет только образ, сохраняя исходные порты, тома и сети. Применяется вместе с
исходными файлами Compose прокси, тем же project name и всеми его исходными env
файлами; `CADDY_IMAGE` берётся из `release.env`. Если сервис называется иначе,
имя в override нужно изменить до применения.

В `Caddyfile.score-it.example` есть безопасные настройки глобальных ошибок и
access log: URI заменяется маркером, заголовки запросов/ответов исключаются.
Сливайте их с существующим Caddyfile, сохранив KidsTime, сертификаты и остальные
глобальные параметры. Не включайте `debug` или `log_credentials`. Проверьте новый
Caddyfile командой `caddy validate --config /etc/caddy/Caddyfile` в одноразовом
контейнере нового образа с теми же необходимыми томами и окружением, затем
пересоздайте только сервис Caddy через его постоянный Compose. Проверьте HTTPS
обоих сайтов после обновления.

Gunicorn/Django маскируют UUID-токены, пути комнат/приглашений и ссылки в traceback.
Браузер получает `Referrer-Policy: strict-origin`; страницы с токенами — `no-store`.
В `Referer` передаётся только origin без пути, query и токенов; ссылки во внешнюю
EVA дополнительно используют `rel="noopener noreferrer"`. Не заменяйте политику
на `no-referrer`: браузерные POST-формы могут отправлять `Origin: null`, который
Django правильно отклоняет. При обновлении измените также заголовок в активном
сайте Caddy; одной пересборки приложения недостаточно, если прокси его перезаписывает.
Старые журналы автоматически не удаляются: ограничьте доступ/срок хранения.
Новые журналы web ротируются (5 файлов по 10 MiB). Ротация ограничивает объём,
но не срок хранения; срок задаёт администратор согласно политике банка.
Уже записанные токены требуют отдельного разбора. При подозрении на доступ к
журналам отзывайте приглашения и обновляйте ссылки участников существующими
средствами приложения. Приглашения имеют срок действия; бессрочность ссылок
продолжения голосования этим изменением не меняется.

## 5. Проверки после установки

### Разграничение доступа и настоящий браузер

`poker.tests.test_access_boundaries` проверяет все именованные маршруты управления
объектами под чужим организатором, анонимным браузером и участником. POST-запросы
используют правильный CSRF, чтобы отказ нельзя было принять за проверку владельца.
Подмена дочернего объекта проверяется также между двумя проектами одного владельца.
Канонический отпечаток бизнес-данных должен сохраняться после запрещённых записей.
Проверяются подмена участника/раунда в теле голосования, сокрытие голосов до раскрытия,
ротация персональной ссылки, старая привязка браузера, истёкшая авторизация и завершённая
комната. Обычная ссылка комнаты остаётся предусмотренным доступом к форме присоединения.

Для HTTPS-сценария с Chromium на **отдельном тестовом Compose-проекте**:

```bash
BROWSER_TOOLS="$(mktemp -d /tmp/score-it-browser-tools.XXXXXXXX)"
cp tests/browser/package.json tests/browser/package-lock.json "$BROWSER_TOOLS/"
npm ci --prefix "$BROWSER_TOOLS" --ignore-scripts --no-audit --no-fund
node "$BROWSER_TOOLS/node_modules/playwright/cli.js" install --with-deps chromium --only-shell
NODE_PATH="$BROWSER_TOOLS/node_modules" python3 tests/check_browser_security.py \
  --project score-it-check-example --release FULL_COMMIT_SHA \
  --database score_it_check_example --output /absolute/browser-security.json
```

Нужны образы текущей проверяемой ревизии и web/db уже запущенного тестового проекта.
Контроллер фикстур сверяется по SHA256 с файлом внутри web. Скрипт отклоняет production-
имя проекта, нештатные теги, опубликованные порты web/db, внешние сети и том БД другого
Compose-проекта. Временный Caddy использует настоящий production-образ и шаблон
`Caddyfile.score-it.example`, `admin off` и HTTPS; публикует только случайный порт
на 127.0.0.1. Тестовый сертификат создаётся на время прогона, проверка его издателя
отключена в тестовом браузере. Проверка доверия публичным сертификатам остаётся
отдельной задачей; этот сценарий подтверждает поведение браузера/Origin через HTTPS.

Два обычных организатора и два участника используют отдельные browser contexts.
Через интерфейс выполняются вход, создание комнаты, присоединение, старт, голоса,
ротация ссылки, раскрытие, сохранение, завершение участниками и организатором и выход.
У настоящих POST-форм проверяется автоматически выставленный браузером Origin.
Отдельные запросы с действительным CSRF проверяют запреты чтения/экспорта/изменения
чужих объектов, подмену участника, отзыв старой ссылки и позднее голосование.
Повторный вход в другой вкладке должен отклонить старую CSRF-форму; после обновления
страницы новая форма должна завершить сессию. Проверяется конечное состояние в БД:
два исходных голоса 2/8, сумма 10, среднее 5, итоговая оценка 4, два участника и
неизменность данных второго организатора.

Пробные учётные записи и связанные записи удаляются, временный Caddy удаляется.
Пароли/ссылки/сырые журналы не попадают в отчёт. CI сохраняет `browser-security.json`
с SHA сборки, идентичностями образов, фактическими версиями браузера/Playwright,
проверками, состоянием БД и результатом очистки. Это ограниченный повторяемый
сценарий безопасности, не полный пентест или решение о банковском допуске.

Для отдельного стенда с пустой тестовой БД есть проверка через настоящий HTTP
Gunicorn (на машине с Docker и Python 3):

```bash
python3 tests/check_live_security.py --project score-it-check-example \
  --release FULL_COMMIT_SHA --database score_it_check \
  --output /absolute/check-result.json
```

Скрипт принимает только отдельный Compose-проект с префиксом `score-it-check-`,
одним healthy web-контейнером нужного релиза, без опубликованных портов и только
с внутренними сетями. Он сверяет имя БД и минимальные права runtime, создаёт две
синтетические staff-учётные записи и удаляет их после проверки. Через HTTP
проверяются CSRF, общий счётчик обоих входов, HTTP 429 после пяти ошибок,
`Retry-After: 900`, блокировка правильного пароля и вход другого сотрудника с
того же IP. Ожидание фактического истечения 15 минут эта проверка не выполняет.

Синтетические ссылки проверяют `no-store`/`strict-origin` и маскирование токенов
в журналах web. Контрольный запрос подтверждает, что нужный участок журнала
получен; проверяется отсутствие тестовых логинов и паролей и наличие событий
входа. JSON-результат не содержит учётных данных или исходных журналов. В БД
стенда остаются только связанные тестовые сессии и записи счётчиков/аудита.
Проверка использует приватный HTTP-переход с HTTPS-заголовком прокси; общий Caddy,
внешний HTTPS и его журналы проверяются отдельно. Это ограниченная проверка
прикладной защиты, а не полный DAST или допуск ИБ.

- Вход старым паролем, открытие проектов/спринтов, голосование, импорт EVA.
- На отдельной тестовой учётной записи: после 5 неудачных попыток на `/login/`
  и `/admin/login/` вход блокируется на 15 минут с HTTP 429. Счётчик хранится в БД
  и общий для процессов/форм/адресов; другой сотрудник за тем же NAT входит.
  Аварийное снятие блокировки: сервис `migrate`, команда `python manage.py axes_reset_username USERNAME`.
- Журнал содержит LOGIN_SUCCESS/FAILURE с HMAC идентификатором, без пароля/логина.
- Запрос по синтетической ссылке комнаты/приглашения не сохраняет её токен в
  журналах Django/Gunicorn/Caddy, включая ошибки проксирования.
- Весь комплект запускается без интернета; подпись, SHA256, платформа и состав
  совпадают с проверенными. Повторно сверяются точные digest и отчёты сканеров.

Остаются отдельные работы из аудита: проверка полной удалённой истории Git,
всех слоёв на секреты (Trivy проверяет видимую файловую систему и конфигурацию,
но не гарантирует проверку удалённых файлов в нижних слоях), DAST на отдельном
стенде, корпоративный SSO и согласование гостевых ссылок/исключений с ИБ.

## 6. Разбор находок Caddy в Scout от 20.10.2026

Для релиза `6b1e47770d389b709ae4a1c32711161cd5cac496` пользователь проверил
`caddy build-info`: фактические Go `1.26.9` и `x/net 0.60.0` совпадают с Trivy.
Scout сообщил Go `1.26.8`/`x/net 0.59.0`, соответствующие исходному официальному
образу. Его старый бинарный файл физически сохранялся в нижнем слое старого
комплекта. Точный механизм выбора версий Scout (слои/SBOM/cache) не установлен;
после изменения упаковки нужен новый отчёт Scout по отдельному архиву нового
образа с проверкой config digest, без исключения находок базового образа.

В Scout 21 пара «идентификатор — пакет» соответствует 16 разным идентификаторам.
Пять CVE повторяются в `stdlib` и `x/net`; это не 21 независимая уязвимость.
Тринадцать CVE привязаны в отчёте к старым Go/x/net: `CVE-2026-94448`,
`CVE-2026-97030`, `CVE-2026-56866`, `CVE-2026-78659`, `CVE-2026-78660`,
`CVE-2026-78667`, `CVE-2026-78669`, `CVE-2026-94439`, `CVE-2026-94440`,
`CVE-2026-97031`, `CVE-2026-97032`, `CVE-2026-78663`, `CVE-2026-56857`.
В отчёте указаны исправленные версии Go `1.26.9` и `x/net 0.60.0`; они уже
используются в пересобранном бинарном файле. `CVE-2026-56857` относится к Windows,
поэтому неприменима к целевой платформе `linux/amd64`:
https://pkg.go.dev/vuln/GO-2026-6604.

Остальные три находки:

- `GO-2026-5932` относится к устаревшим пакетам `x/crypto/openpgp`. Сопоставление
  SHA256 бинарного файла runtime/builder и `go list -deps` показало
  `OPENPGP_NOT_IMPORTED`; сам модуль `x/crypto 0.57.0` нужен другим компонентам.
  Сохраняйте исходную находку и доказательство применимости, не исключайте весь
  модуль: https://pkg.go.dev/vuln/GO-2026-5932.
- `CVE-2025-60876` описывает обработку URL в BusyBox wget. В старом образе
  присутствовал BusyBox `1.37.0-r30`; Alpine помечает этот пакет как
  `possibly vulnerable`. Новый финальный образ не содержит BusyBox или wget:
  https://security.alpinelinux.org/vuln/CVE-2025-60876.
- `CVE-2026-58055` описывает поведение прокси `nghttpx`. В старом образе
  установлен `nghttp2-libs 1.69.0-r0`, а не подтверждённый сервис `nghttpx`.
  Наличие библиотеки не доказывает применимость сценария к Caddy. В новом
  финальном образе нет nghttpx, libnghttp2 или использовавшего библиотеку curl:
  https://security.alpinelinux.org/vuln/CVE-2026-58055.

До пересборки и повторного Scout/Trivy эти изменения не закрывают находки нового
комплекта. Находки Debian в приложении/PostgreSQL, полная история Git, секреты
нижних слоёв остальных образов и допуск ИБ остаются отдельными проверками.


## Extended audit evidence

CI fetches advertised branch, tag and pull-request head refs into a full clone,
then runs pinned Gitleaks 8.30.1 across all reachable history. Deleted or
unadvertised refs and external Git LFS payloads are outside this scope. The
recorded refs and commit count identify the exact checked history.

The isolated release job also scans every regular file in every saved image
layer separately, including overwritten and deleted content, plus image config
and archive member/link metadata. Layer diff IDs and image config digests are
verified against the saved release. Links are never followed. The documented
limits are 4 GiB of regular payload per image, 250,000 entries per layer, nested
archive depth 3, and decode depth 5. Scanner failure or findings block CI.

Repository ignore files, inline allows and baselines are disabled. Built-in
Gitleaks rules and allowlists still apply. Additional exceptions are the
exact public Python release signing fingerprint
`7169605F62C751356D054A26A821E680E5FA6305`, documented in
https://github.com/docker-library/python/blob/master/Dockerfile-linux.template .
It is neither a private key nor an authentication credential. Findings reports
retain only rule, path, line and commit metadata; matched values and source
lines are never published.

Live staging HTTP checks cover anonymous access to private routes, CSRF rejection
for mutation routes, secure CSRF cookies, framing/MIME headers, login lockout and
log token privacy. These are bounded automated checks, not a full penetration
test or approval of residual OS CVEs.


### Go crypto/x509 refresh (2026-10-21)

The first PR #19 scan blocked delivery on CVE-2026-111943 in the Go 1.26.9
standard library embedded in gosu and Caddy. Both builders now pin Go 1.26.10
and its verified official-image index digest. Runtime build-info checks require
that version; the HIGH/CRITICAL Go release gate remains enabled. Earlier scan
records above describe the prior binaries, not the rebuilt release.

References: https://go.dev/doc/devel/release#go1.26.10 and the exact Trivy JSON
retained for the failed candidate in Actions run 39570909958.


DLA-4845-1 also appeared for the Python image CA bundle during this scan. The
app build upgrades only `ca-certificates` to `20250419~deb12u2`, using normal
Debian signed repository verification; an apt simulation rejects removal or
changes to other packages. Both certificate-trust advisories are explicit
release blockers. The PostgreSQL image does not ship the ca-certificates package.

Caddy also retains its build-time package dependency list and the SHA256 of the
built executable. Archive checks require that hash to match the delivered
binary and reject any `golang.org/x/crypto/openpgp` import, including subpackages.
This is build provenance evidence for GO-2026-5932, not a blanket waiver of
other x/crypto advisories.

Image path/link metadata is scanned as original text values separated by blank lines, not concatenated JSON fields. This avoids matches manufactured by adjacent unrelated metadata fields without excluding original names or link targets.


The path audit also reviews two exact Debian package metadata filenames: the
amd64 `.triggers` files for libgssapi-krb5-2 and libkeyutils1. The generic API-key
rule interprets their colon-delimited architecture suffix as a credential.
Only those complete lines in the generated member-paths.txt are excepted. Their
file contents, other paths and ordinary credentials remain scanned. This was
checked against the real pinned scanner with a generated synthetic credential
that must still be detected. Fully redacted match context is retained for any
unresolved findings; no matched secret value is written into published reports.


The PostgreSQL layer scan additionally reviewed one public C macro documentation
line in the Perl cop.h header. Only the exact line naming COPHH_KEY_UTF8 in that
header is excepted; other comments and header contents remain scanned. The
upstream source is https://github.com/Perl/perl5/blob/v5.36.0/cop.h . A detector
control with a synthetic credential remains positive. All three layer scans are
collected before the job fails on any unresolved finding.
