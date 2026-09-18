# Выпуск и проверка релиза

## Ветки и окружения

- `main`: проверенный код, изменения только через PR и CI.
- `develop`: интеграционная ветка; `feature/*` и обычные fixes направляются сюда.
- `develop` -> PR в `main` после проверки. Hotfix из `main` возвращается в `develop`.
- Теги `vX.Y.Z` и `vX.Y.Z-rc.N`: неизменяемые версии. Не используйте `latest` для эксплуатации.
- Root `docker-compose.yml` и root `.env`: разработка, localhost HTTP.
- `deploy/compose.yml` и `deploy/.env`: эксплуатация, отдельный project/volume,
  `APP_ENV=production`, HTTPS. На одном хосте не переиспользуйте production project name.

В репозитории обеих веток остаются tests, dev-зависимости, AGENT, миграции и инструкции.
В runtime-образ включаются только app, migrations, alembic.ini, runtime dependencies
и LICENSE. Никаких production-секретов в Git/образах/Actions artifacts.

## Подготовка GitHub (владелец)

1. Опубликуйте ветку `develop` после проверки изменений. Включите защиту `main`:
   PR, запрет force-push/удаления и обязательные CI jobs `tests`, `secrets`, `image`.
   Для тегов `v*` настройте ruleset, запрещающий переписывание и удаление выпущенных версий.
2. Включите private vulnerability reporting в Security settings. Настройте environment
   `release` с required reviewer, если ваш тариф поддерживает эту возможность.
3. Разрешите workflow публикации пакетов в GHCR. Настройте видимость package отдельно
   от репозитория: публичный репозиторий не делает уже существующий package публичным.
4. Не выдавайте секреты deployment-сервера workflow. Публикация использует временный
   `GITHUB_TOKEN`, pull-request проверки имеют только `contents: read`.
5. CI использует GitHub Actions, закрепленные по проверенным SHA; Dependabot обновляет
   их в `develop`. Следующий шаг hardening: фиксация base images по проверенным digest.
   Единственное локальное исключение Checkov CKV_GHA_7 разрешает параметр версии ручного
   релиза: он проверяется регулярным выражением и принадлежностью тега main, не попадает
   в shell-код через прямую подстановку и не задает произвольные build arguments.

## Проверка RC

CI запускается на PR, push в main/develop и вручную. Он проверяет:

- Python 3.12, установку зависимостей и `pip check`, pytest и Ruff;
- Alembic на чистом PostgreSQL 16, повторный upgrade и startup приложения;
- Bandit Medium/High (`-ll`), аудит runtime Python dependencies;
- Gitleaks по всем fetched refs с полной историей и redaction;
- сборку runtime-образа, non-root и отсутствие development/secrets в нем;
- изолированный production stack с временными случайными секретами и тестовым TLS;
- HTTPS, Secure cookie, CSRF, вход, security headers, закрытый OpenAPI и ICMP из worker.
- Backup/restore disposable PostgreSQL stack и повторный вход после восстановления.

Low findings Bandit по-прежнему оценивайте вручную полным запуском из README.
CI не заменяет восстановление ваших собственных backup, обновление реальной предыдущей версии
на обезличенной копии БД и ручную проверку UI/прав/нагрузки. Не загружайте боевую БД в CI.

Локально сначала выполните проверки из CONTRIBUTING. Docker-проверку запускайте на
отдельном стенде с другим Compose project name и портом. Нельзя направлять smoke-test
на production: он использует bootstrap-учетные данные тестового окружения.

## Публикация (только после зеленых проверок)

1. Обновите CHANGELOG: номер, дата, несовместимости, миграции и процедура обновления.
2. Объедините проверенный PR в main, выберите commit и создайте annotated tag:

```bash
git switch main
git pull --ff-only
git tag -a v0.1.0-rc.1 -m "IPtable v0.1.0-rc.1"
git push origin v0.1.0-rc.1
```

Эти команды являются инструкцией, агент не публикует теги без отдельного поручения.
3. Запустите `Publish release image` из main с этим тегом. Workflow проверяет, что
   тег относится к main, повторяет CI и только затем публикует `linux/amd64` образ
   `ghcr.io/<owner>/<repository>:v0.1.0-rc.1` с OCI metadata, SBOM и provenance.
   Имя registry/repository переводится в нижний регистр; существующий образ версии
   не должен перезаписываться. Не пересоздавайте Git-теги, выпускайте новую версию.
4. Запишите digest опубликованного образа в GitHub Release, приложите изменения,
   ссылку на INSTALL, совместимость архитектуры и статус RC/prerelease.
5. Проверьте `docker pull` образа без вашей личной авторизации на отдельной машине,
   установку по INSTALL, backup/restore и обновление. После приемки выпускайте стабильный тег.

Первый RC не считается проверенным или опубликованным только потому, что добавлен
workflow. Отчет должен отдельно показывать локальные проверки, GitHub CI, Docker
и реальные опубликованные артефакты. Registry publication здесь ручная, auto-deploy нет.

## Перед открытием репозитория

```bash
git fetch --all --tags
gitleaks git --redact=100 --log-opts=--all --no-banner
gitleaks dir --redact=100 --no-banner .
```

Не прикладывайте неотредактированные отчеты с секретами к публичным issue.
Проверка касается доступных Git refs, не гарантирует очистку форков, чужих клонов
или удаленных объектов. При находке сначала отзовите/замените секрет; удаление
из текущего файла не удаляет его из истории. Переписывание истории согласуется отдельно.
Отдельно просмотрите скриншоты, имена клиентов, реальные сети и экспортированные файлы:
они могут быть конфиденциальны, даже если scanner их не считает секретами.
