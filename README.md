# IPtable

IPtable - веб-приложение для учета занятых IP-адресов в локальных сетях. Проект заменяет Excel-таблицы для инвентаризации сетевых активов: можно создавать папки, заводить проекты-подсети, вести карточки IP-адресов и регулярно проверять доступность адресов через ICMP ping.

## Что умеет проект

- Создание папок/групп для логической организации сетей.
- Создание проектов-подсетей, например `172.16.16.0/24`.
- Автоматическое создание строк таблицы для usable IP-адресов подсети без network/broadcast адресов.
- Ручное заполнение полей `hostname`, `OS`, `type`, `comment`.
- Создание дополнительных пользовательских столбцов для проекта администратором или пользователем с отдельным правом.
- Авторизация пользователей через страницу входа.
- Админ-панель `/admin/users` для создания обычных пользователей и назначения прав.
- Редактирование пользователей администратором: логин, пароль, имя, фамилия, описание, активность и права. Администратора из `.env` отключить или удалить нельзя, а его логин и права управляются `.env`.
- Ролевая модель: обычные пользователи могут редактировать IP-строки, а создание, редактирование/удаление папок и проектов, а также управление столбцами выдаются отдельными правами.
- Автоматическое завершение сессии после 24 часов бездействия.
- Импорт проекта из CSV с разделителем `;` и форматом `ip;hostname;os;type;comment`.
- Экспорт одной подсети в CSV, а папки - в ZIP-архив с CSV-файлами проектов. ZIP можно защитить паролем.
- Скрытие незаполненных IP-записей в таблице проекта по умолчанию. Ping-статус сам по себе не считается заполнением строки.
- Защита от потери несохраненных изменений: измененная строка подсвечивается красным, а кнопка `Save` появляется только для нее.
- История изменений IP-записей: кто, когда, какой IP и какое поле изменил.
- Общий поиск по папкам, проектам и IP-записям.
- Фоновая проверка доступности IP-адресов через отдельный worker-сервис и DB-очередь задач.
- Расписания ping-проверок для отдельных проектов и целых папок.
- Защита от флуда: проекты проверяются последовательно, адреса идут пакетами с ограниченным параллелизмом и паузами.
- Первый ping-проход ставится в очередь сразу после создания проекта-подсети.
- Современный светлый интерфейс с верхним поиском, выходом, боковой панелью и таблицей в стиле улучшенной Excel-замены.
- Запуск локально через Docker Compose.
- Скрипты резервного копирования и восстановления PostgreSQL.

## Архитектура проекта

Проект сделан как веб-приложение с отдельным worker-сервисом:

- `FastAPI` обслуживает HTML-интерфейс, формы и healthcheck.
- `SQLAlchemy` описывает модели и работает с базой данных.
- `PostgreSQL` хранит пользователей, папки, проекты, IP-записи и пользовательские поля.
- История изменений IP-записей хранится в `ip_address_history`.
- `Alembic` управляет миграциями схемы БД; таблицы не создаются через `create_all` при старте web.
- `Jinja2` используется для серверного HTML-рендеринга.
- `SessionMiddleware` хранит подписанную cookie-сессию авторизованного пользователя.
- Idle-timeout сессии контролируется настройкой `SESSION_IDLE_TIMEOUT_SECONDS`; по умолчанию 24 часа.
- Пользователь из `INITIAL_ADMIN_USERNAME` является администратором и получает все права при старте приложения.
- `app/services/csv_io.py` отвечает за проверку CSV, расчет подсети, генерацию CSV и ZIP-архивов.
- `worker` запускает `python -m app.worker`, читает расписания/очередь из БД и выполняет ICMP-проверки отдельно от web.
- Реальные ping-timeout ответы записываются как `NO`. Ошибки запуска `ping` логируются и оставляют адрес в `NoTest`.
- Статусы ping в UI: `NoTest` - не тестировалось, `OK` - доступно, `NO` - недоступно.
- Для защиты сети от всплесков ICMP используются `PING_CONCURRENCY`, `PING_BATCH_SIZE`, `PING_BATCH_PAUSE_SECONDS`, `PING_PROJECT_PAUSE_SECONDS` и `PING_QUEUE_POLL_SECONDS`.
- Очередь ping-задач сейчас DB-backed (`ping_jobs`), без Redis. Для нескольких worker-экземпляров лучше добавить Redis/Celery/RQ или PostgreSQL locking.

## Структура директорий

```text
.
├── app/
│   ├── core/              # настройки и подключение к БД
│   ├── services/          # бизнес-логика: подсети, custom fields, ping
│   ├── static/            # CSS
│   ├── templates/         # HTML-шаблоны
│   ├── web/               # веб-маршруты
│   ├── main.py            # точка входа FastAPI
│   ├── worker.py          # отдельный ping-worker
│   └── models.py          # SQLAlchemy-модели
├── tests/                 # базовые тесты
├── migrations/            # Alembic-миграции БД
├── scripts/               # backup/restore PostgreSQL
├── alembic.ini
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── AGENT.md
└── README.md
```

## Используемые технологии

- Python 3.12
- FastAPI
- SQLAlchemy 2
- Alembic
- PostgreSQL 16
- Jinja2
- itsdangerous / signed session cookies
- Docker Compose
- pytest / unittest-compatible tests

## Требования для запуска

Для запуска через Docker:

- Docker
- Docker Compose

Для запуска без Docker:

- Python 3.12+
- PostgreSQL или SQLite для локальной разработки
- Установленная системная команда `ping`

## Настройка `.env`

Скопируйте пример:

```bash
cp .env.example .env
```

Минимально нужно задать надежные значения для БД, cookie-сессии и первого администратора:

```env
POSTGRES_PASSWORD=replace-with-strong-local-password
SECRET_KEY=replace-with-random-32-plus-character-secret
INITIAL_ADMIN_PASSWORD=replace-with-strong-admin-password
```

Основные переменные:

- `APP_PORT` - порт веб-приложения на хосте.
- `SECRET_KEY` - секрет для подписи session cookie. В production должен быть стабильным и случайным.
- `SESSION_IDLE_TIMEOUT_SECONDS` - время жизни авторизованной сессии бездействия. По умолчанию `86400` секунд.
- `INITIAL_ADMIN_USERNAME` - логин администратора, которому при старте выдаются административные права. Если логин администратора в `.env` изменится, старый администратор будет понижен до обычного пользователя.
- `INITIAL_ADMIN_PASSWORD` - пароль администратора. Используется только для создания пользователя, если его еще нет.
- `DATABASE_URL` - строка подключения SQLAlchemy.
- `PING_INTERVAL_SECONDS` - интервал фоновой проверки адресов.
- `PING_TIMEOUT_SECONDS` - timeout одного ping.
- `PING_CONCURRENCY` - максимальное количество одновременных ping внутри одного пакета.
- `PING_BATCH_SIZE` - размер пакета IP-адресов внутри проекта.
- `PING_BATCH_PAUSE_SECONDS` - пауза между пакетами адресов.
- `PING_PROJECT_PAUSE_SECONDS` - пауза между проектами/подсетями.
- `PING_QUEUE_POLL_SECONDS` - как часто worker проверяет очередь и расписания.
- `MAX_PROJECT_ADDRESSES` - максимум IP-адресов в одном проекте.
- `CSV_IMPORT_MAX_BYTES` - максимальный размер CSV-файла для импорта.
- `ENABLE_PING_WORKER` - включает встроенный worker внутри web-процесса. В Docker Compose для `web` он выключен, а отдельный сервис `worker` включен.

## Запуск через Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Docker Compose запускает четыре сервиса:

- `postgres` - база данных.
- `migrate` - разово выполняет `alembic upgrade head`.
- `web` - веб-интерфейс FastAPI.
- `worker` - отдельный ping-worker с ICMP capability `NET_RAW`.

После запуска приложение будет доступно:

```text
http://localhost:8000
```

Если в `.env` изменен `APP_PORT`, используйте выбранный порт.

## Запуск без Docker

Для быстрой локальной разработки можно использовать SQLite:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p data
export DATABASE_URL=sqlite:///./data/iptable.sqlite3
export ENABLE_PING_WORKER=false
export SECRET_KEY=local-random-secret-change-me
export INITIAL_ADMIN_PASSWORD=local-admin-password-change-me
alembic upgrade head
uvicorn app.main:app --reload
```

Приложение будет доступно на `http://127.0.0.1:8000`.

В отдельном терминале можно запустить ping-worker:

```bash
export DATABASE_URL=sqlite:///./data/iptable.sqlite3
export SECRET_KEY=local-random-secret-change-me
export INITIAL_ADMIN_PASSWORD=local-admin-password-change-me
python -m app.worker
```

## Основные команды

```bash
docker compose up --build        # запустить проект
docker compose down              # остановить контейнеры
docker compose logs -f web       # смотреть логи приложения
docker compose logs -f worker    # смотреть логи ping-worker
docker compose logs -f postgres  # смотреть логи PostgreSQL
alembic upgrade head             # применить миграции без Docker
scripts/backup_postgres.sh       # создать backup PostgreSQL в ./backups
python -m pytest                 # запустить тесты после установки зависимостей
python -m unittest discover      # запустить базовые тесты через stdlib
```

## Резервное копирование и восстановление PostgreSQL

Создать резервную копию PostgreSQL в формате custom dump:

```bash
scripts/backup_postgres.sh
```

По умолчанию файл будет создан в `backups/iptable_YYYYmmdd_HHMMSS.dump`. Можно передать свой путь:

```bash
scripts/backup_postgres.sh backups/manual_before_upgrade.dump
```

Восстановление выполняется в работающий сервис `postgres` и является разрушительной операцией для текущих таблиц:

```bash
docker compose stop web worker
CONFIRM_RESTORE=YES scripts/restore_postgres.sh backups/manual_before_upgrade.dump
docker compose start web worker
```

Папка `backups/` добавлена в `.gitignore` и не должна коммититься.

## Как проверить, что проект работает

1. Откройте `http://localhost:8000`.
2. Войдите под пользователем из `INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_PASSWORD`.
3. Откройте `Админ` / `/admin/users`, создайте обычного пользователя и при необходимости включите права `создание`, `редактирование`, `удаление`, `столбцы`, либо отключите пользователя через чекбокс активности.
4. Создайте папку, например `Office`.
5. Создайте проект с CIDR `172.16.16.0/24`.
6. Для импорта нажмите `Импортировать из CSV` в форме создания проекта и загрузите файл с заголовком `ip;hostname;os;type;comment`.
7. Откройте проект и нажмите `Показать скрытые`, если проект новый и все строки пока пустые.
8. Заполните `hostname`, `OS`, `type` или `comment` у нескольких адресов.
9. Убедитесь, что измененная строка подсвечивается красным и кнопка `Save` появляется только у нее.
10. Откройте `История` в проекте и убедитесь, что изменение строки записалось.
11. Проверьте экспорт проекта через кнопку `Экспорт`, а экспорт папки через иконку экспорта рядом с папкой.
12. Проверьте поиск через верхнюю форму.
13. Для проекта или папки откройте расписание `Ping`, измените интервал или включите `Запустить сейчас`.
14. Проверьте `docker compose logs -f worker`: worker должен забирать задачи из очереди.
15. Выполните `scripts/backup_postgres.sh` и убедитесь, что появился файл в `backups/`.
16. Откройте `http://localhost:8000/health` и убедитесь, что вернулся статус `ok`.

## Типичные проблемы и их решение

- `POSTGRES_PASSWORD is required`: скопируйте `.env.example` в `.env` и задайте пароль.
- Docker build падает на `Temporary failure in name resolution` или `No matching distribution found for fastapi`: это проблема DNS/доступа к внешним репозиториям внутри Docker, а не версия пакета. Проверьте `docker run --rm python:3.12-slim getent hosts deb.debian.org pypi.org`. Если DNS не работает, настройте DNS Docker daemon или proxy на сервере.
- Не получается войти: проверьте `INITIAL_ADMIN_USERNAME` и `INITIAL_ADMIN_PASSWORD`. Если пользователь уже создан, смена переменной `INITIAL_ADMIN_PASSWORD` не меняет существующий пароль.
- Нет кнопки `Админ`: в админ-панель может попасть только пользователь, которому при старте выдан `is_admin` по `INITIAL_ADMIN_USERNAME`.
- Обычный пользователь не может создать папку, проект или столбец: включите нужное право в админ-панели. По умолчанию эти права отключены.
- Пользователь больше не может войти: проверьте, не отключен ли он в админ-панели.
- Сессия завершилась: пользователь был неактивен дольше `SESSION_IDLE_TIMEOUT_SECONDS`.
- Сессии сбрасываются после перезапуска: задайте постоянный `SECRET_KEY` в `.env`.
- Web падает с сообщением `Run alembic upgrade head`: миграции не применены. В Docker это делает сервис `migrate`; без Docker выполните `alembic upgrade head`.
- CSV не импортируется: проверьте заголовок `ip;hostname;os;type;comment`, разделитель `;`, отсутствие дублей IP и network/broadcast адресов.
- ZIP с паролем создается без внешних Python-зависимостей. Это совместимый традиционный ZIP-пароль; для более строгого шифрования в будущем можно перейти на 7z/AES.
- Приложение не подключается к БД: проверьте `DATABASE_URL`, имя сервиса `postgres` и логи `docker compose logs postgres`.
- Ping всегда показывает offline: worker-контейнеру нужен ICMP-доступ. В `docker-compose.yml` для `worker` добавлен `NET_RAW`, но сеть или firewall могут блокировать ICMP.
- Ping остается `NoTest`: проверьте логи `docker compose logs -f worker`. Если там `Operation not permitted`, пересоберите образ: Dockerfile выдает `/usr/bin/ping` capability `cap_net_raw`.
- Ping-задачи не выполняются: проверьте `docker compose logs -f worker`, настройки `ENABLE_PING_WORKER`, расписание проекта/папки и таблицу `ping_jobs`.
- История изменений пустая: она фиксирует только новые сохранения строк после внедрения этой функции.
- Backup не создается: проверьте, что сервис `postgres` запущен, а у пользователя есть права на запись в каталог `backups/`.
- Restore остановился с предупреждением: нужно явно передать `CONFIRM_RESTORE=YES`, чтобы подтвердить разрушительное восстановление.
- Все адреса после проверки стали `NO`: проверьте ICMP из контейнера/сервера. Ping-статус больше не делает пустую строку заполненной, поэтому скрытие пустых строк не ломается.
- Слишком большая подсеть не создается: увеличьте `MAX_PROJECT_ADDRESSES`, если понимаете нагрузку. По умолчанию стоит защитный лимит.
- Порт занят: поменяйте `APP_PORT` в `.env`.

## Что ещё нужно доработать в будущем

- Добавить импорт/экспорт XLSX.
- Добавить массовое редактирование и теги активов.
- Добавить REST API для интеграции с внешними системами.
- Добавить Redis/Celery/RQ или PostgreSQL advisory locks для безопасного горизонтального масштабирования worker.
- Добавить историю ping-задач и страницу мониторинга очереди.
- Добавить более строгое шифрование экспорта через 7z/AES при необходимости.
- Добавить виртуализацию/пагинацию таблицы для очень крупных подсетей.

## Другие важные моменты

- Проект сейчас рассчитан на локальную или внутреннюю сеть.
- Перед production-использованием обязательно добавьте HTTPS, политику смены паролей и резервное копирование PostgreSQL.
- Не коммитьте `.env`, локальные базы, логи и артефакты сборки.
- При изменении архитектуры, команд запуска или переменных окружения обновляйте `README.md` и `AGENT.md`.
