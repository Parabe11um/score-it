# Score It

Веб-приложение для Planning Poker и планирования спринтов. Организатор работает под своей учётной записью, участники подключаются к голосованию без регистрации по уникальной ссылке.

## Возможности MVP

- проекты и массовая загрузка задач в формате `номер | название`;
- пакетное добавление задач в упорядоченную очередь голосования;
- асинхронное прохождение очереди каждым участником в своём темпе;
- индивидуальная навигация назад/вперёд с сохранением выбранных оценок;
- настраиваемый минимум голосов перед раскрытием карт;
- автоматический переход к следующей задаче после сохранения оценки;
- публичные комнаты голосования;
- вход участника по имени без учётной записи;
- шкала `0, 2, 4, 8, 12, 20, 32, 52`;
- скрытые голоса, раскрытие и повторный раунд;
- сохранение точного среднего как суммы голосов и их количества;
- спринты, плановая ёмкость и сумма оценок;
- выгрузка спринта в Excel;
- фоновое обновление через HTTP polling без Redis и WebSocket;
- локальный запуск в Docker и production-запуск через Passenger/WSGI.

## Быстрый запуск в Docker

```bash
cp .env.example .env
docker compose up --build
```

После запуска приложение будет доступно по адресу <http://localhost:8000>.

Создание организатора:

```bash
docker compose exec web python manage.py createsuperuser
```

## Запуск без Docker

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Тесты

```bash
python manage.py test
```

## Как проходит пакетная оценка

1. Импортируйте в проект список задач.
2. При создании комнаты выберите задачи и укажите минимум голосов. Все неоценённые задачи уже отмечены.
3. Отправьте участникам одну публичную ссылку и нажмите «Начать оценку». Голосование откроется сразу по всей очереди.
4. Каждый участник проходит задачи независимо, используя кнопки «Назад» и «Вперёд». Голоса и текущая позиция сохраняются.
5. Организатор видит прогресс по каждой задаче. После достижения минимума он раскрывает карты и сохраняет точную среднюю оценку.

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `DJANGO_SECRET_KEY` | Секретный ключ Django; обязателен в production |
| `DJANGO_DEBUG` | `1` локально, `0` в production |
| `DJANGO_ALLOWED_HOSTS` | Домены через запятую |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | HTTPS-адреса через запятую |
| `DJANGO_TIME_ZONE` | Часовой пояс, по умолчанию `Europe/Moscow` |
| `DATABASE_PATH` | Путь к SQLite-файлу |

## Production на REG.RU

Проект рассчитан на Python 3.12 и Phusion Passenger. Общая последовательность развёртывания:

```bash
git clone git@github.com:Parabe11um/score-it.git
cd score-it
/opt/python/python-3.12/bin/python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
```

При последующих обновлениях:

```bash
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
touch .restart-app
```

В панели хостинга нужно выбрать Python 3.12, указать корень приложения и точку входа `passenger_wsgi.py`. Точные пути и домен заполняются при первой публикации.
