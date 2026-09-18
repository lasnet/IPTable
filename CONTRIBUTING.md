# Разработка IPtable

Пользовательская установка: [docs/INSTALL.md](docs/INSTALL.md).
Выпуск версий: [docs/RELEASING.md](docs/RELEASING.md).

Создавайте ветки `feature/<task>` от `develop`, отправляйте PR в `develop`.
Стабильные изменения попадают в `main` через проверенный PR. Не удаляйте тесты и
документацию из main для уменьшения production-образа: его состав задает Dockerfile.

## Локальный запуск

Следуйте разделу README «Запуск без Docker» или используйте корневой Compose:

```bash
cp .env.example .env
# Задайте свои локальные секреты, не копируйте production .env.
docker compose -p iptable-development up -d --build
```

Приложение доступно на `http://127.0.0.1:8000`. PostgreSQL не публикуется.
Для уже существующей установки сохраняйте прежнее имя Compose project, пока
не перенесли данные: смена имени создает другой volume.
Для backup/restore development задайте `COMPOSE_PROJECT_NAME=iptable-development`.
Не используйте production DB, secrets, TLS keys или реальные клиентские данные.

## Проверки перед PR

```bash
python -m pip install -r requirements-dev.txt
python -m pip check
python -m pytest -q
python -m ruff check .
python -m bandit -r app scripts
python -m pip_audit -r requirements.txt --cache-dir .cache/pip-audit
gitleaks git --redact=100 --log-opts=--all --no-banner
```

Полный набор security-проверок есть в README. Для изменений auth/session/admin/
export/upload/Docker дополнительно проверьте CSRF, rate limit, секреты, порты и OpenAPI.
Не выключайте проверки ради зеленого CI. Исключения должны быть узкими и объясненными.
CI блокирует Bandit Medium/High; Low требуют ручного просмотра, а не игнорирования.

Сохраняйте существующие service/router/template границы. Новые поля БД добавляйте
Alembic-миграцией. Обновляйте README, AGENT и env examples при изменении их контрактов.
Пользовательские строки добавляйте в RU/EN переводы. Новые зависимости обосновывайте.

Проект распространяется под MIT, см. LICENSE. Не отправляйте код/данные третьих лиц,
на распространение которых у вас нет прав. Уязвимости сообщайте по SECURITY.md.
