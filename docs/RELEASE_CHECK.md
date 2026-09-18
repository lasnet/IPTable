# Проверка подготовки первого RC

Дата: 2026-09-18. Локальная проверка подготовки релиза дополнена GitHub CI
для commit `85cc33eaf195122b7188c6b7644dd04ed9d84c2a` в PR #1.
Отчет фиксирует проверки на дату подготовки, а не текущий реестр опубликованных версий.

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

- Локальный Docker daemon отсутствует, но GitHub CI уже проверил сборку и полный
  PostgreSQL/Nginx/worker stack. Локальный HTTPS-тест использовал Uvicorn/SQLite.
- Перенос данных конкретной прежней установки требует отдельного стенда с ее копией.
- Backup/restore добавлен в последующие CI-запуски; его результат смотрите в job image
  выбранного релизного commit, а не в первоначальном запуске ниже.
- Тесты показывают существующие deprecation warnings (`datetime.utcnow`, httpx TestClient).
- Состояние тега, GHCR digest и публичность package проверяйте по release notes и
  анонимному pull. CI на PR не означает публикацию артефакта.

## GitHub CI и настройки

[CI run 35372235636](https://github.com/lasnet/IPTable/actions/runs/35372235636):
jobs `tests`, `secrets`, `image` успешно завершены. Подтверждены Python 3.12,
PostgreSQL migrations/startup, production Docker image, HTTPS/CSRF/login и ICMP.

Для main включены обязательный PR и проверки tests/secrets/image, актуальность ветки,
разрешенные conversations и запрет force-push/deletion, в том числе для администратора.
Владелец сейчас один, поэтому дополнительное одобрение второго пользователя не требуется.
Теги v* защищены от изменения и удаления. Environment release требует подтверждения
владельца и допускает только защищенные ветки. Private vulnerability reporting включен.

Статус: начальная подготовка проверена локально и в GitHub CI. Готовность конкретного
RC определяется его финальным CI и публикацией образа. Результаты scanner-ов актуальны
только на дату проверки; перед выпуском повторяйте checklist в RELEASING.md.
