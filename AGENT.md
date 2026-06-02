# AGENT.md

## 1. Назначение проекта

IPtable - внутреннее веб-приложение для учета IP-адресов и сетевых активов в локальных сетях. Оно заменяет Excel-таблицы: хранит папки, проекты-подсети, строки IP-адресов, ручные атрибуты активов и статус доступности по ICMP.
Доступ к основным страницам закрыт авторизацией: пользователь входит через `/login`, после чего хранится подписанная cookie-сессия.

## 2. Главная бизнес-логика

- Папка группирует проекты.
- Проект - это подсеть в CIDR-формате, например `172.16.16.0/24`.
- `Project.name` - человекочитаемое название подсети. Оно отображается в заголовке рабочей области проекта, поиске и форме редактирования; в боковой панели для компактности показывается CIDR.
- Проекты в боковой панели сортируются по числовому значению CIDR/network address через `_project_sidebar_sort_key`, а не по `created_at` или `id`.
- В sidebar обычное состояние строки папки показывает только chevron, folder icon и имя. Действия папки/проекта открываются через hover/focus меню `⋯`; не возвращайте отдельные action-иконки в строку без сильной UX-причины. Формы создания проекта, экспорта и настройки ping должны открываться в modal, а не встраиваться внутрь sidebar.
- При создании проекта приложение нормализует CIDR и создает строки только для usable host-адресов. Для обычной `/24` сети адреса `.0` и `.255` не создаются.
- Базовые поля IP-записи: порядковый номер, IP-адрес, hostname, OS, type, comment.
- `ordinal` и `address` генерируются автоматически и не редактируются через UI.
- Остальные базовые поля заполняются вручную.
- Пользовательские столбцы хранятся как metadata в `custom_fields`, а значения - в JSON-поле `ip_addresses.custom_values`.
- Обычный авторизованный пользователь может редактировать строки IP-таблицы.
- Администратор - пользователь из `INITIAL_ADMIN_USERNAME`; при старте он получает `is_admin=True` и все права. Старые пользователи с `is_admin=True`, не совпадающие с текущим env-логином, понижаются до обычных пользователей.
- Обычные пользователи создаются через `/admin/users`. Чекбоксы прав по умолчанию выключены.
- Администратор может редактировать обычных пользователей, менять им пароль, права, активность и удалять их. У env-админа через UI можно менять пароль/описание, но нельзя менять логин, права, отключить или удалить учетную запись.
- Все POST-формы защищены CSRF-токеном из session cookie. Новые POST-формы обязаны добавлять `{{ csrf_input(request) }}`.
- `/login` защищен DB-backed rate limit по IP+логину через таблицу `login_rate_limit_events`. Настройки: `LOGIN_RATE_LIMIT_ATTEMPTS`, `LOGIN_RATE_LIMIT_WINDOW_SECONDS`, `LOGIN_RATE_LIMIT_LOCKOUT_SECONDS`.
- Язык web-интерфейса задается через `INTERFACE_LANGUAGE`: поддерживаются `RU` и `EN`, значение нормализуется к `RU` при неизвестном вводе.
- Права обычных пользователей:
  - `can_create` - создание папок и проектов;
  - `can_edit` - редактирование названий папок, названия/описания проектов;
  - `can_delete` - удаление папок и проектов;
  - `can_manage_columns` - добавление пользовательских столбцов в проекты.
- Пустые IP-строки по умолчанию скрыты. Ping-статус не считается заполнением строки; скрытие смотрит на hostname, OS, type, comment и custom fields.
- Сообщение "Пустые строки скрыты..." показывается под таблицей всегда, когда включен `hide_empty`, даже если отображается одна или несколько заполненных строк.
- Изменения IP-записей логируются в `ip_address_history`: адрес, поле, старое/новое значение, пользователь и время. История пишется только при сохранении строки, если значение реально изменилось. `/projects/{id}/history` показывает историю проекта, `/folders/{id}/history` - историю всех проектов внутри папки.
- Авторизованная сессия истекает после `SESSION_IDLE_TIMEOUT_SECONDS` секунд бездействия; по умолчанию это 24 часа.
- Импорт CSV/XLSX доступен администратору или пользователю, у которого одновременно включены `can_create` и `can_edit`.
- CSV импортируется с разделителем `;`, XLSX - с первой строки заголовков. Поддерживаются колонки `ip`, `hostname`, `os`, `type`, `comment`; подсеть проекта рассчитывается по минимальной сети, которая покрывает импортированные IPv4-адреса.
- Экспорт доступен только администратору. Проект экспортируется в CSV/XLSX или ZIP с одним файлом, если включен пароль. Папка экспортируется в ZIP с отдельным CSV/XLSX для каждого проекта.
- ZIP с паролем создается с AES-шифрованием через `pyzipper`.
- Ping-worker работает через DB-backed очередь `ping_jobs` и расписания `ping_schedules`.
- На PostgreSQL очередь worker защищена advisory locks: lock `0` внутри namespace IPTable используется для обслуживания расписаний, а id задачи - для claim конкретной `ping_jobs` записи. Это позволяет безопасно запускать несколько worker-экземпляров без Redis/Celery/RQ.
- SQLite fallback advisory locks не использует и предназначен для локальной разработки с одним worker.
- Если worker аварийно завершился и оставил задачу в `running`, следующий worker вернет ее в `queued` после `PING_RUNNING_JOB_TIMEOUT_SECONDS`.
- В Docker Compose ping вынесен в отдельный сервис `worker`; web только ставит задачи в очередь. Встроенный worker внутри web остается fallback-режимом через `ENABLE_PING_WORKER=true`.
- Ping-worker защищен от флуда: проекты выполняются отдельными задачами, внутри проекта адреса проверяются пакетами. Управляющие настройки: `PING_CONCURRENCY`, `PING_BATCH_SIZE`, `PING_BATCH_PAUSE_SECONDS`, `PING_PROJECT_PAUSE_SECONDS`, `PING_QUEUE_POLL_SECONDS`.
- После создания проекта в очередь ставится немедленная ping-проверка адресов этого проекта.
- Администратор может настроить расписание ping для проекта или папки; папочная проверка ставит задачи для всех проектов внутри папки.
- Ping-worker пишет реальные timeout-ответы как `NO`. Если сам запуск `ping` вернул системную ошибку, адрес остается `NoTest`, а причина пишется в лог.
- В UI используются статусы `NoTest`, `OK`, `NO`.
- Таблица проекта использует серверную пагинацию: по умолчанию `PROJECT_TABLE_DEFAULT_PAGE_SIZE`, в UI доступны 25/50/100/250 строк. Поиск по IP передает нужную страницу в ссылке, чтобы найденная строка сразу была в DOM.
- Фильтры таблицы по ping-статусу, `type` и `OS` применяются на backend и сохраняются в URL, AJAX-пагинации, выборе размера страницы и сохранении строки.
- Переключение страниц таблицы выполняется AJAX-запросом partial-шаблона `_project_table.html`, без полного перерендера страницы.
- Header проекта содержит компактный summary-блок, а таблица IP имеет right drawer деталей строки. Не перегружайте строки отдельными постоянными кнопками: действия строки должны оставаться в меню `⋯`.
- Действие `Clear` для IP-строки очищает только ручные поля и custom fields, но не сбрасывает ping-статус.
- REST API `/api/v1/*` включается только при заданном `INTEGRATION_API_TOKEN` и принимает `X-API-Key` или `Authorization: Bearer`.
- Фронтенд-защита от потери данных находится в `app/static/app.js`: измененная строка подсвечивается красным, кнопка `Save` появляется только у dirty row, переход к другой строке предупреждает о несохраненных данных.
- Первый администратор создается из `INITIAL_ADMIN_USERNAME` и `INITIAL_ADMIN_PASSWORD`, если такого пользователя еще нет.
- Резервные копии PostgreSQL создаются через `scripts/backup_postgres.sh`; восстановление через `scripts/restore_postgres.sh` требует `CONFIRM_RESTORE=YES`.

## 3. Архитектура проекта

Проект является FastAPI-приложением с отдельным worker-процессом:

- `app/main.py` создает приложение, подключает статику, роуты и lifecycle, добавляет security headers и отключает `/docs`, `/redoc`, `/openapi.json` при `APP_ENV=production`.
- `app/core/config.py` читает настройки из окружения.
- `app/core/database.py` создает SQLAlchemy engine/session и проверяет, что Alembic-миграции уже применены.
- `app/models.py` содержит SQLAlchemy-модели.
- `app/api/routes.py` содержит REST API для внешних интеграций. API отключен, если `INTEGRATION_API_TOKEN` пустой.
- `app/web/routes.py` содержит HTML-роуты и form-handlers.
- `app/worker.py` запускает отдельный ping-worker.
- `app/services/` содержит бизнес-логику без привязки к HTML, включая auth, ping и работу с подсетями.
- `app/templates/` содержит Jinja2-шаблоны.
- `app/static/` содержит CSS.
- `migrations/` содержит Alembic-миграции; новые изменения схемы добавляйте миграцией, а не `create_all`/ручными `ALTER TABLE` на старте.

База данных по умолчанию PostgreSQL. Для локальной разработки поддерживается SQLite через `DATABASE_URL=sqlite:///./data/iptable.sqlite3`.

## 4. Структура директорий

```text
.
├── app/
│   ├── core/
│   │   ├── config.py
│   │   └── database.py
│   ├── services/
│   │   ├── custom_fields.py
│   │   ├── csv_io.py
│   │   ├── auth.py
│   │   ├── inventory.py
│   │   ├── network.py
│   │   └── ping.py
│   ├── static/
│   │   ├── app.js
│   │   └── styles.css
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html
│   │   ├── login.html
│   │   ├── project.html
│   │   ├── admin_users.html
│   │   └── search.html
│   ├── web/
│   │   └── routes.py
│   ├── main.py
│   ├── worker.py
│   └── models.py
├── migrations/
│   └── versions/
└── scripts/
    ├── backup_postgres.sh
    └── restore_postgres.sh
```

## 5. Основные файлы и за что они отвечают

- `Dockerfile` - сборка Python-образа с системной командой `ping`.
- `docker-compose.yml` - сервисы `postgres`, `migrate`, `web` и `worker`.
- `.env.example` - пример настроек без реальных секретов.
- `requirements.txt` - Python-зависимости.
- `requirements-dev.txt` - dev/test/security tooling.
- `README.md` - публичная инструкция по запуску и эксплуатации.
- `AGENT.md` - инструкция для будущего ИИ-агента или разработчика.
- `app/services/auth.py` - PBKDF2-хеширование паролей, проверка логина и bootstrap первого администратора.
- `app/services/csv_io.py` - импорт CSV/XLSX, расчет CIDR по IP-адресам, рендер CSV/XLSX и создание ZIP/AES ZIP с паролем.
- `app/services/security.py` - CSRF helpers и DB-backed rate limiter для login.
- `app/services/i18n.py` - словарь переводов интерфейса RU/EN и helpers для Jinja/backend сообщений.
- `app/services/history.py` - построение diff и запись истории изменений IP-записей.
- `app/models.py` - модели БД, включая `User` и computed properties прав доступа.
- `app/templates/admin_users.html` - админ-панель создания пользователей и просмотра выданных прав.
- `app/templates/base.html` - общий layout, верхняя панель, боковая панель папок/проектов и UI создания/редактирования папок по правам.
- `app/templates/project.html` - рабочая область проекта, таблица IP, добавление столбцов и настройки проекта по правам.
- `app/templates/_project_table.html` - partial таблицы проекта для полной страницы и AJAX-пагинации.
- `app/templates/history.html` - просмотр последних изменений IP-записей проекта.
- `app/web/routes.py` - HTML-роуты, form-handlers, проверки авторизации и прав.
- `app/services/ping.py` - ICMP-проверки, OS-specific ping flags, DB-очередь ping-задач, PostgreSQL advisory locks для worker и расписания.
- `app/worker.py` - entrypoint отдельного ping-worker.
- `migrations/versions/` - Alembic-ревизии схемы БД.
- `app/services/network.py` - нормализация CIDR и генерация usable host-адресов.
- `app/static/app.js` - dirty-state таблицы, предупреждение о несохраненных строках, AJAX-пагинация и фильтры таблицы, включение/отключение поля пароля в формах экспорта и прокрутка к найденной IP-строке.
- `app/templates/login.html` - страница входа.
- `tests/test_network.py` - тесты генерации IP-адресов и валидации CIDR.
- `tests/test_custom_fields.py` - тесты ключей пользовательских полей.
- `tests/test_auth.py` - тесты проверки паролей, computed permissions и bootstrap администратора.
- `tests/test_csv_io.py` - тесты разбора CSV/XLSX, расчета подсети и ошибок импорта.
- `tests/test_api.py` - тест REST API и токен-защиты.
- `tests/test_history.py` - тесты записи истории изменений.
- `scripts/backup_postgres.sh` - `pg_dump -Fc` backup из Docker Compose сервиса `postgres`.
- `scripts/restore_postgres.sh` - восстановление dump через `pg_restore --clean --if-exists`, требует `CONFIRM_RESTORE=YES`.

## 6. Как запускать проект

Через Docker:

```bash
cp .env.example .env
# обязательно задайте SECRET_KEY и INITIAL_ADMIN_PASSWORD
docker compose up --build
```

Compose сначала запускает `postgres`, затем одноразовый `migrate` с `alembic upgrade head`, после этого стартуют `web` и `worker`.
`postgres`, `web` и `worker` имеют `restart: unless-stopped`, чтобы переживать reboot сервера. `migrate` остается одноразовым job и не должен иметь постоянный restart policy.

Если Docker build не может резолвить `deb.debian.org` или `pypi.org`, проверьте DNS Docker daemon или proxy на сервере. При наличии старого успешного билда не используйте `--no-cache`, чтобы Docker мог переиспользовать cached layers.

Без Docker:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
mkdir -p data
export DATABASE_URL=sqlite:///./data/iptable.sqlite3
export ENABLE_PING_WORKER=false
export SECRET_KEY=local-random-secret-change-me
export INITIAL_ADMIN_PASSWORD=local-admin-password-change-me
export SESSION_IDLE_TIMEOUT_SECONDS=86400
alembic upgrade head
uvicorn app.main:app --reload
```

Отдельный worker без Docker:

```bash
export DATABASE_URL=sqlite:///./data/iptable.sqlite3
export SECRET_KEY=local-random-secret-change-me
export INITIAL_ADMIN_PASSWORD=local-admin-password-change-me
python -m app.worker
```

## 7. Как запускать тесты

После установки зависимостей:

```bash
python -m pytest
```

Без pytest можно выполнить текущие базовые тесты:

```bash
python -m unittest discover
```

Security-проверки:

```bash
python -m bandit -r app scripts
python -m pip_audit --cache-dir .cache/pip-audit --local
python -m ruff check .
semgrep --metrics=off --config p/python app scripts
checkov -f docker-compose.yml --framework yaml --skip-download --compact
gitleaks detect --redact --source .
trivy fs --scanners secret,misconfig .
hadolint Dockerfile
```

`.vscode/tasks.json` можно держать локально для запуска этих команд из VS Code. Каталог `.vscode/` намеренно игнорируется Git.
`bandit` запускайте под Python 3.12/3.13. На Python 3.14 у текущего `bandit==1.8.6` есть upstream-проблема совместимости с AST, из-за которой часть файлов может быть пропущена.

## 8. Как добавлять новые функции

1. Сначала проверьте, относится ли функция к бизнес-логике, веб-слою или инфраструктуре.
2. Бизнес-логику добавляйте в `app/services/`.
3. Новые модели добавляйте в `app/models.py` и обязательно создавайте Alembic-миграцию в `migrations/versions/`.
4. Новые страницы добавляйте через `app/web/routes.py` и `app/templates/`.
5. Новые настройки добавляйте в `app/core/config.py`, `.env.example`, `README.md` и `AGENT.md`.
6. Новые интерфейсные строки добавляйте через `app/services/i18n.py`, не хардкодьте RU/EN напрямую в шаблонах или JS.
7. Добавляйте тесты для логики, которую можно проверить без БД или с тестовой БД.
8. Если меняете авторизацию или права, проверяйте редирект на `/login`, `/admin/users`, создание пользователя и запреты для обычного пользователя без прав.
9. Административные действия должны быть защищены на backend-роутах, а не только скрыты в шаблонах.
10. Изменения схемы БД должны проходить через Alembic. Не возвращайте `Base.metadata.create_all()` в startup web.
11. При изменениях auth/session/admin/export/upload/Docker обязательно запускайте security-проверки и отдельно проверяйте CSRF, rate limit, секреты, публичные порты и OpenAPI exposure.

## 9. Какие правила кодстайла использовать

- Python 3.12+.
- Типизируйте функции, где это повышает читаемость.
- Используйте SQLAlchemy 2 style: `Mapped`, `mapped_column`, `select`.
- Не смешивайте HTML-логику и бизнес-правила, выносите правила в `services`.
- Комментарии добавляйте только для неочевидной логики.
- Не добавляйте зависимости без необходимости.
- Ошибки пользовательского ввода показывайте через UI или понятный HTTP-ответ.
- Пароли храните только в виде PBKDF2-хеша, не логируйте пароли и session secret.
- Не логируйте пароль ZIP-экспорта, `INTEGRATION_API_TOKEN` и содержимое импортируемых CSV/XLSX.
- Не добавляйте POST-формы без `csrf_input(request)`.
- Не включайте OpenAPI UI в `APP_ENV=production`.

## 10. Какие файлы нельзя менять без необходимости

- `Dockerfile` - влияет на сборку и установку системных/Python-зависимостей. Любое изменение до `apt-get` сбивает кэш apt-слоя.
- `docker-compose.yml` - влияет на запуск всей системы.
- `alembic.ini` и `migrations/` - контракт миграций БД.
- `.env.example` - является контрактом настроек.
- `app/models.py` - изменение схемы БД требует осторожности.
- `app/core/database.py` - влияет на подключение и lifecycle БД.
- `app/services/auth.py` - влияет на безопасность входа.
- `app/services/security.py` - влияет на CSRF и rate limit.
- `README.md` и `AGENT.md` - обязаны оставаться актуальными.

## 11. Какие данные нельзя коммитить

- `.env` и любые реальные секреты.
- `SECRET_KEY`, реальные пароли пользователей и экспортированные cookie.
- Локальные SQLite-файлы в `data/`.
- PostgreSQL dumps с реальными данными.
- Логи, временные файлы, кеши.
- Персональные IDE-настройки.
- Экспортированные таблицы с приватной инвентаризацией.
- Файлы `backups/` и любые PostgreSQL dumps.

## 12. Как обновлять документацию

Обновляйте `README.md`, если изменились:

- способ запуска;
- Docker Compose;
- команды;
- переменные окружения;
- пользовательские возможности.

Обновляйте `AGENT.md`, если изменились:

- архитектура;
- структура директорий;
- бизнес-логика;
- правила разработки;
- текущий статус или roadmap.

## 13. Частые ошибки и важные нюансы

- По умолчанию при создании проекта network и broadcast адреса не генерируются для обычных подсетей. Для `10.10.10.0/24` таблица начинается с `10.10.10.1`.
- Новый проект сначала может показывать пустую таблицу, потому что пустые строки скрыты по умолчанию. Для заполнения нажмите `Показать скрытые`.
- Существующие reserved rows удаляются при старте приложения в `normalize_project_address_rows`; оставшиеся адреса перенумеровываются.
- Папки в левой панели закрыты по умолчанию. Не добавляйте `open` без отдельной UX-причины.
- Папка активного проекта открывается автоматически, чтобы переход в подсеть не сворачивал контекст.
- Кнопка `Save` должна быть отдельной колонкой, не внутри `Статус (Ping)`, и показываться только у измененной строки.
- Столбец `№` скрыт в `app/templates/project.html` через `show_number_column = false`. Если включаете его обратно, проверьте комментарий рядом с флагом и верните sticky offset `.locked-col` на `74px`.
- `INITIAL_ADMIN_PASSWORD` используется только при создании нового пользователя. Изменение переменной не сбрасывает пароль уже созданного пользователя.
- Изменение `INITIAL_ADMIN_USERNAME` переносит роль администратора на новый env-логин; старый админ теряет `is_admin` и явные права, которые выдавались bootstrap-ом.
- `SESSION_IDLE_TIMEOUT_SECONDS` управляет idle-timeout и cookie `max_age`; при каждом авторизованном запросе обновляется `last_activity_at` в session cookie.
- Если `SECRET_KEY` не задан в `APP_ENV=production`, приложение не стартует. В local-режиме без секрета будет временный ключ на процесс, поэтому сессии сбросятся после перезапуска.
- OpenAPI UI доступен только вне production. При `APP_ENV=production` `/docs`, `/redoc` и `/openapi.json` отключены.
- Ошибка Docker build `Temporary failure in name resolution` означает, что контейнер сборки не видит DNS для Debian/PyPI. Это не проблема версии `fastapi`; настройте Docker DNS/proxy на сервере.
- Импорт CSV/XLSX отклоняет неправильный заголовок, не-IPv4 адреса, дубли IP, network/broadcast адреса, слишком большую рассчитанную подсеть и слишком большой файл.
- Для ручного создания проекта поле CIDR обязательно; для импорта CSV/XLSX CIDR не требуется и рассчитывается автоматически.
- Экспорт проекта без пароля возвращает CSV или XLSX. Экспорт проекта с паролем возвращает AES ZIP с одним файлом. Экспорт папки всегда возвращает ZIP.
- История изменений не заполняется задним числом для старых записей; она появляется только после новых сохранений строк.
- Скрипт восстановления PostgreSQL разрушительно заменяет данные в текущей БД. Перед запуском остановите `web` и `worker`, затем требуйте явное `CONFIRM_RESTORE=YES`.
- Подсети ограничены `MAX_PROJECT_ADDRESSES`, чтобы случайно не создать огромную таблицу.
- ICMP может быть заблокирован firewall или сетевой политикой Docker.
- Dockerfile устанавливает `libcap2-bin` и выдает `/usr/bin/ping` capability `cap_net_raw+ep`, чтобы non-root пользователь `app` мог выполнять ICMP.
- На Linux ping использует timeout в секундах, на macOS/Windows - в миллисекундах; это важно для локального запуска без Docker.
- Не увеличивайте `PING_CONCURRENCY` без оценки нагрузки. Для продакшена с десятками `/24` безопаснее увеличивать интервал или паузы между пакетами/проектами, чем параллелизм.
- `PING_RUNNING_JOB_TIMEOUT_SECONDS` должен быть больше ожидаемого времени проверки самой большой подсети. Иначе долгий, но живой job может быть поставлен в очередь повторно.
- В Docker capability `NET_RAW` добавлен только сервису `worker`.
- Dockerfile и Compose содержат healthcheck. Для `migrate` healthcheck отключен, потому что это одноразовый job.
- Если после reboot сервера `web` и `worker` постоянно рестартуют, а `postgres` имеет `Exited (0)`, значит база не поднялась автоматически. У `postgres` должен быть `restart: unless-stopped`; после обновления compose выполните `docker compose up -d`.
- Если web или worker падают с `Run alembic upgrade head`, схема БД не применена. В Docker за это отвечает сервис `migrate`; локально выполните `alembic upgrade head`.
- Несколько worker-экземпляров можно запускать с PostgreSQL: claim задач и обслуживание расписаний защищены advisory locks. В SQLite-режиме оставляйте один worker. При увеличении числа worker-реплик пересчитывайте общий ICMP-параллелизм: фактическая нагрузка примерно равна `PING_CONCURRENCY * количество_worker`.
- Таблица проекта не должна рендерить все IP-записи сразу. Сохраняйте `page`, `per_page` и `hide_empty` в ссылках/формах, если добавляете новые действия внутри таблицы.
- Админ-панель находится на `/admin/users`. В UI ссылка `Админ` показывается только администратору.
- Env-админ защищен от удаления; его логин и права задаются `.env`, но пароль/описание можно менять через UI.
- Env-админ всегда активен при bootstrap. Обычных пользователей можно отключать в админ-панели.
- Обычные пользователи без `can_create` не видят плюсы создания папок/проектов, а backend также отклоняет POST.
- Обычные пользователи без `can_manage_columns` не видят кнопку `Столбец`, а backend также отклоняет POST.
- Обычные пользователи без `can_edit`/`can_delete` не видят меню редактирования/удаления папок и проектов, а backend также отклоняет POST.
- При создании проекта web создает строки IP, создает расписание проекта и ставит ping-задачу в очередь. Сама ICMP-проверка выполняется worker-ом.

## 14. Текущий статус проекта

Статус: рабочий MVP.

Реализовано:

- папки;
- авторизация через login/logout;
- CSRF-защита POST-форм;
- DB-backed rate limiting на login;
- security headers и отключение OpenAPI UI в production;
- проекты-подсети;
- генерация IP-таблицы;
- редактирование IP-записей;
- дополнительные столбцы;
- история изменений IP-записей;
- история изменений на уровне папки;
- админ-панель создания пользователей;
- отключение пользователей без удаления;
- редактирование, смена пароля и удаление обычных пользователей в админ-панели;
- права на создание, редактирование, удаление и управление столбцами;
- редактирование/удаление папок и проектов для пользователей с правами;
- idle-timeout сессии 24 часа бездействия;
- импорт проекта из CSV/XLSX;
- экспорт проекта/папки в CSV/XLSX/ZIP, включая AES ZIP с паролем;
- удаление legacy-колонки `ip_addresses.tags` из схемы БД;
- RU/EN интерфейс через `INTERFACE_LANGUAGE`;
- REST API для внешних интеграций;
- скрытие пустых строк;
- фильтры таблицы по ping-статусу, `type` и `OS`;
- новый светлый SaaS-интерфейс с верхним поиском и боковой панелью;
- общий поиск;
- фоновые ICMP-проверки;
- отдельный ping-worker, DB-очередь и расписания проектов/папок;
- PostgreSQL advisory locks для безопасного горизонтального масштабирования worker;
- DB-backed rate limiting login для нескольких web-реплик;
- автоматический requeue зависших `running` ping-задач;
- серверная пагинация таблицы проекта;
- AJAX-подгрузка страниц таблицы без полного перерендера страницы;
- Alembic-миграции;
- резервное копирование и восстановление PostgreSQL через scripts;
- Docker Compose;
- Docker healthchecks;
- dev security tooling;
- базовые тесты;
- документация.

## 15. TODO / Roadmap

- Добавить мониторинг очереди ping-задач и историю выполнений.
- Добавить ротацию и аудит использования `INTEGRATION_API_TOKEN`.
- Добавить REST API endpoint-ы для создания папок/проектов и импорта файлов, если внешние интеграции потребуют write-flow шире обновления IP-записей.
- Добавить фильтры таблицы по пользовательским столбцам.
- Вернуть массовое редактирование после уточнения UX и прав доступа, если оно понадобится в эксплуатации.
- Добавить экспорт истории изменений и историю выполненных ping-задач.
