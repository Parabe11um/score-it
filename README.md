# score-it

Оценка задач в часах и планирование спринтов: проекты, комнаты голосования,
импорт CSV/XLSX из EVA, команда и ёмкость, квоты, история и экспорт.

Организатор входит под своей учётной записью. Участники подключаются по ссылке
комнаты; персональная ссылка позволяет продолжить голосование на другом устройстве.
Проекты и операции управления изолированы по владельцу.

- [Работа с приложением: голосование, команда, EVA и приглашения](docs/user-guide.md).
- [Production: локальная сборка и установка готового комплекта](deploy/README.md).
- [Защитные настройки и границы проверок](deploy/SECURITY.md).

## Разработка

Нужны Python 3.12 либо локальный Docker. Это окружение разработки, использующее
SQLite; production работает через Gunicorn и отдельный PostgreSQL.

    cp .env.example .env
    docker compose up --build

Приложение: http://localhost:8000. Создать организатора:

    docker compose exec web python manage.py createsuperuser

Без Docker:

    python3.12 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    cp .env.example .env
    python manage.py migrate
    python manage.py createsuperuser
    python manage.py runserver

Основные настройки разработки описаны в .env.example. Для production используется
отдельный deploy/env.production.example; секреты задаются в окружении установки.

## Проверки

Из исходного репозитория с установленными зависимостями:

    python manage.py check
    python manage.py makemigrations --check --dry-run
    python manage.py test poker.tests
    python -m unittest discover -s tests -p 'test_*.py' -v
    node --test tests/task-picker.test.cjs

CI также выполняет PostgreSQL-тесты, сборку трёх образов, проверку загрузки релиза,
браузерное голосование через HTTPS, синтетический импорт с реальными пределами
ресурсов, анализ зависимостей и поиск секретов в истории Git и слоях образов.

Тесты остаются в репозитории. Production-образ содержит только код приложения,
миграции, статику, шаблоны и три runtime-скрипта из deploy. Проверочные инструменты
передаются извне только во временные тестовые контейнеры/проверенный стенд.

## Production

Сборка и скачивание зависимостей выполняются на отдельной машине с интернетом.
На сервер передаётся готовый комплект с images.tar, Compose, отчётами и SHA256SUMS.
Сервер проверяет и загружает локальные образы; сборка и скачивание образов при
запуске запрещены. Полный порядок — в [deploy/README.md](deploy/README.md).

Исходный Git-репозиторий, Dockerfile разработки и команды установки Python-пакетов
на production-сервер не переносятся. HTTPS в закрытом контуре обеспечивает
корпоративный прокси/PKI; отсутствие исходящего интернета подтверждается отдельной
приёмкой с сетевым запретом.
