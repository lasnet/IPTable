# Разработка IPtable

Этот файл и AGENT.md находятся только в dev. Пользовательская инструкция - README.md.

## Ветки

- stable - ветка по умолчанию, приложение для пользователей.
- dev - разработка, тестирование, тесты и служебные инструкции.

Для отдельной тестовой установки:

```bash
git clone --branch dev https://github.com/lasnet/IPTable.git IPTable-dev
cd IPTable-dev
docker compose -p iptable-dev up -d --build
```

Сначала заполните пароли и SECRET_KEY в Compose или .env. При одновременном запуске
stable и dev на одном сервере задайте dev другой APP_PORT. Имя iptable-dev отделяет
его контейнеры, образ и volume от пользовательской установки.

## Перенос проверенных изменений в stable

Закоммитьте и отправьте изменения в dev, дождитесь CI, проверьте интерфейс на своем
стенде. Затем из чистой рабочей копии dev:

```bash
python3 scripts/update_stable.py          # посмотреть состав переноса
python3 scripts/update_stable.py --push   # выполнить merge в stable
```

Команда создает обычный merge commit с двумя родителями: предыдущим stable и dev.
Приложение, Compose, README и миграции переносятся из dev; инструкции разработчика,
тесты, dev-зависимости и CI остаются в dev. Дополнительные ветки и релизные теги
не создаются, force-push не используется. Рабочая директория не переключается.

Это заменяет ручное исключение служебных файлов при merge. Обычный merge через
кнопку GitHub также перенесет измененные служебные файлы, поэтому для сохранения
короткого состава stable используйте эту команду. Если в stable отдельно исправили
код, сначала перенесите исправление в dev: скрипт откажется его затереть.

## Запуск без Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export APP_ENV=local
export DATABASE_URL=sqlite:///./data/iptable.sqlite3
export ENABLE_PING_WORKER=false
export SECRET_KEY=local-development-key-change-me
export INITIAL_ADMIN_PASSWORD=local-development-password-change-me
alembic upgrade head
uvicorn app.main:app --reload
```

Для отдельного worker с тем же окружением: `python -m app.worker`.
SQLite используется только для локальной разработки с одним worker.
В Docker по умолчанию PostgreSQL и отдельный worker.

## Проверки

```bash
python -m pytest -q
python -m ruff check .
python -m bandit -r app scripts
python -m pip_audit -r requirements.txt --cache-dir .cache/pip-audit
semgrep --metrics=off --config p/python app scripts
checkov -f Dockerfile --framework dockerfile --skip-download --compact
checkov -f docker-compose.yml --framework yaml --skip-download --compact
gitleaks git --redact=100 --log-opts=--all --no-banner
trivy fs --scanners secret,misconfig .
hadolint Dockerfile
```

CI в dev проверяет тесты, Ruff, зависимости, секреты, сборку и реальную установку
через корневой Compose, вход по HTTP, ping и backup/restore PostgreSQL.
Bandit Medium/High блокирует CI; Low проверяйте вручную. Не отключайте правила
глобально ради зеленого отчета. Semgrep/Checkov и остальные команды запускаются
также при изменениях auth/session/admin/export/upload/Docker.
Локальные .vscode/tasks.json и отчеты проверок не коммитятся.

## Настройки и данные

Все настройки Docker находятся в корневом Compose. .env необязателен и подставляет
значения в выражения `${NAME:-default}`. Внутри приложения DATABASE_URL имеет
приоритет; без него POSTGRES_HOST/PORT/USER/PASSWORD/DB собираются через SQLAlchemy
URL.create, поэтому специальные символы пароля корректно кодируются.
Без DATABASE_URL и POSTGRES_HOST сохраняется локальный SQLite fallback.

APP_ENV=production выключает OpenAPI и проверяет секреты. Для прямого HTTP в
Compose SESSION_COOKIE_SECURE=false; для своего HTTPS proxy установите true.
При прямом запуске production без явной настройки secure-cookie прежнее поведение
сохраняется: Secure cookie и HSTS включены. Изменение SECRET_KEY сбрасывает сессии.

Не меняйте имя Compose project существующей установки без переноса данных:
volume привязан к имени project. Никогда не подключайте dev к пользовательской
базе. Backup/restore запускайте с тем же Compose project, что и приложение.

## Изменения кода

Бизнес-логика - app/services, маршруты - app/web и app/api, интерфейс -
app/templates и app/static. Для схемы БД добавляйте Alembic-миграции.
Новые UI-строки должны иметь RU/EN перевод. При изменении настроек обновляйте
README, .env.example, Compose и AGENT; подробности разработки оставляйте здесь.

## Дальнейшие улучшения

- Мониторинг ping-очереди и heartbeat worker.
- Защита от перезаписи одной IP-записи несколькими пользователями.
- Предпросмотр импорта и сохраненные фильтры.
- Browser-регрессии таблицы и drawer.
- UTC-aware даты вместо datetime.utcnow.
- Ротация токенов интеграции и экспорт истории.
