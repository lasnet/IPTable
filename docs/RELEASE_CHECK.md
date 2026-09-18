# Проверка подготовки первого RC

Дата: 2026-09-18. Проверено локальное рабочее дерево ветки `develop`, основанной
на `e6c2386`. Изменения подготовки релиза еще не являются опубликованным тегом.

## Выполнено

- pytest: 76 passed, 17 subtests passed (локальный Python 3.14).
- Ruff: ошибок нет; `pip check`: конфликтов установленных зависимостей нет.
- Bandit: 0 Medium/High, 8 Low для ручного анализа, без пропущенных файлов.
- pip-audit runtime requirements: известных уязвимостей не найдено на дату проверки.
- Semgrep p/python: 0 findings; новые deployment-скрипты также проверены отдельно.
- Checkov Dockerfile: 85 passed; workflows: 131 passed, 1 документированное
  исключение CKV_GHA_7 для параметра версии ручного релиза.
- Actionlint 1.7.12: оба workflow прошли проверку.
- Compose CLI 5.5.1: production-конфигурация валидна; нет ports у web/PostgreSQL,
  APP_ENV=production, HTTPS по умолчанию привязан к loopback, database network internal.
- Gitleaks 8.30.1: 45 коммитов всех доступных локальных refs, утечек не найдено.
  Дополнительно проверены новые deploy/scripts/workflows. Удаленные refs не обновлялись.
- Применены все Alembic-миграции к отдельной временной SQLite-БД.
- Реальный HTTPS smoke-тест с временными случайными секретами: health, headers,
  Secure cookie, CSRF, вход администратора, запрет docs/redoc/openapi. Сервер остановлен.
- `git diff --check`, синтаксис shell-скриптов и ignore-правила приватных файлов проверены.

## Еще не подтверждено

- Docker daemon отсутствует: сборка образа и запуск полного PostgreSQL/Nginx/worker
  stack локально не выполнялись. Эти проверки предусмотрены в CI, но CI еще не запущен.
- Локальный HTTPS-тест использовал Uvicorn/SQLite, не Nginx/PostgreSQL.
- Проверка на Python 3.12, backup/restore и перенос прежней БД требуют отдельного стенда.
- Тесты показывают существующие deprecation warnings (`datetime.utcnow`, httpx TestClient).
- Тег, GHCR-образ и GitHub Release не созданы; публичная доступность package не проверена.
- Защита веток/тегов, approval environment и private vulnerability reporting требуют
  настройки владельцем GitHub-репозитория.

Статус: подготовка релиза проверена в доступном локальном окружении. Это **не**
подтверждение готовности опубликованного production-артефакта. Перед выпуском выполните
CI и ручной checklist в RELEASING.md; результаты scanner-ов актуальны только на дату проверки.
