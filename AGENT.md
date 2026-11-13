# AGENT.md

## 1. Назначение проекта

IPtable - простой self-hosted учет IP-адресов и сетевых активов, замена Excel.
Установка пользователя: clone, настройка Compose, docker compose up -d --build.
Не возвращайте обязательные сертификаты, registry, отдельный production Compose
или длинные release-инструкции в пользовательский сценарий.

## 2. Главная бизнес-логика

Папки содержат проекты-подсети. Для CIDR генерируются usable host-адреса;
network/broadcast для обычных подсетей исключаются. IP и ordinal не редактируются.
Ручные поля: hostname, OS, type, comment, пользовательские столбцы.
Ping не считается заполнением строки; пустые записи скрыты по умолчанию.

Редактирование IP только в right drawer по клику на строку. Save появляется после
Edit; dirty-изменения защищены предупреждением. Custom fields сохраняются атомарно;
удаление очищает значения, смена типа проверяет существующие данные.
Изменения IP пишутся в историю только при реальном изменении.

INITIAL_ADMIN_USERNAME задаёт защищённого администратора установки; bootstrap-пароль
используется только при первом создании. Другим аккаунтам можно назначить роль
администратора. Роли пользователя, редактора и пользовательские роли не дают доступ
к управлению аккаунтами, ролями или общим настройкам. Четыре разрешения ролей
управляют созданием, редактированием, удалением и столбцами инвентаря.
Администратора установки нельзя удалить, отключить или лишить роли. Изменять его
пароль может только он сам. Не ослабляйте backend-проверки прав.

CSV/XLSX импорт: ip;hostname;os;type;comment, заголовок необязателен.
Импорт разрешен админу или при can_create + can_edit. Экспорт только админу,
защищенный ZIP использует AES. Сохраняйте защиту CSV/XLSX от формул.

## 3. Архитектура проекта

FastAPI/Jinja2, SQLAlchemy, Alembic, PostgreSQL, отдельный ping-worker с DB-очередью.
Корневой docker-compose.yml запускает postgres, migrate, web, worker по HTTP.
Встроенный HTTPS gateway опционален: HTTPS_ENABLED и COMPOSE_FILE из .env.example
выбирают deploy/https/true.yml; false.yml пустой. Это одинаково для dev и stable.
Redis/Nginx и Python на машине пользователя не требуются.

## 4. Структура директорий

app/core - настройки/БД; app/services - бизнес-логика; app/web и app/api - маршруты;
app/templates и app/static - UI; migrations - схема; scripts - backup/restore
и служебные команды dev; tests - тесты; .github/workflows/ci.yml - CI dev.

stable содержит только приложение, инструкции пользователя, Compose и runtime-файлы.
AGENT.md, DEVELOPMENT.md, tests, requirements-dev.txt, .github и служебные
update_stable.py/smoke_test.py остаются только в dev.

## 5. Основные файлы

app/core/config.py читает bootstrap Settings, валидирует секреты и строит URL PostgreSQL
через URL.create при POSTGRES_HOST без DATABASE_URL. Явный DATABASE_URL приоритетен;
без обоих используется SQLite. Не выводите URL с паролем в логи.
app/main.py настраивает auth cookies, security headers и OpenAPI.
app/services/security.py - CSRF и DB-backed login rate limit.
app/services/ping.py - очереди, advisory locks, расписания и probes.
app/services/settings.py - общие настройки из БД, ревизии и API-токен.
Личные тема, язык и число строк хранятся в users; пустые значения наследуют
настройки установки. Название приложения меняется только через app.manage.
app/manage.py запускает Alembic и однократный импорт прежних env-значений.
scripts/update_stable.py - перенос проверенного dev в stable с сохранением merge-истории.

## 6. Запуск

Пользователь: заполнить три секрета в Compose или .env, docker compose up -d --build.
Для dev на том же сервере: другой APP_PORT и docker compose -p iptable-dev up -d --build.
Локальный запуск без Docker и полный набор команд описаны только в DEVELOPMENT.md.

## 7. Тесты

python -m pytest -q; python -m ruff check .
При auth/session/admin/export/upload/Docker запускайте security-команды из DEVELOPMENT.md:
проверьте CSRF, rate limit, секреты, опубликованные порты и доступность OpenAPI.
CI также проверяет реальный Compose по HTTP/HTTPS, CSS/JS, редиректы, скрытие версии
Nginx, ping и backup/restore PostgreSQL.

## 8. Добавление функций

Следуйте существующим service/router/template границам, добавляйте миграции и
тесты по риску изменения. Новые UI-строки переводите на RU/EN.
После тестов и CI код из dev переносится в stable через scripts/update_stable.py.
Команда сохраняет обоих родителей merge, не переносит dev-файлы и отказывает
при самостоятельных изменениях stable, которых еще нет в dev.
Не создавайте автоматические release/feature/dependabot ветки без необходимости.

## 9. Кодстайл

Python 3.12+, понятные имена, SQLAlchemy 2 style, типы на границах.
Новые зависимости только по необходимости. Комментарии - для неочевидной логики.
Ручные изменения файлов вносите через apply_patch.

## 10. Что менять осторожно

Модели/миграции, auth/security, настройки, Dockerfile, Compose и update_stable.py.
Не используйте create_all в старте приложения вместо Alembic.
Не меняйте имена volumes существующей установки автоматически.
HTTP по умолчанию привязан к loopback; сетевой bind задаётся явно.
MAX_REQUEST_BODY_BYTES ограничивает полный body до разбора multipart.
POSTGRES_USER используется для миграций/backup, POSTGRES_APP_USER — для web/worker.
Пароль runtime-роли вычисляется из SECRET_KEY; migrate выдаёт DML/sequence grants
на новых и существующих томах. Внешней DB нужны отдельные runtime/migration URL.
Production startup отклоняет административную DB-роль web/worker.

## 11. Что нельзя коммитить

.env, реальные секреты, TLS-ключи, дампы БД, инвентаризацию клиентов, cookie,
логи, локальные базы, кеши и персональные IDE-настройки.

## 12. Документация

README - описание продукта, установка и настройки для пользователя.
DEVELOPMENT.md - все подробности разработки. AGENT.md - инструкции агенту.
При изменении настроек обновляйте README, .env.example, Compose и этот файл.
Не размножайте пользовательскую документацию по отдельным release/security/install файлам.

## 13. Нюансы

APP_ENV=production сохраняется в Compose: OpenAPI выключен, секреты проверяются.
APP_PUBLIC_URL определяет Secure cookie/HSTS для HTTP, встроенного HTTPS и внешнего
proxy. Без URL прямой production-запуск сохраняет Secure cookie по умолчанию.
Внешний proxy должен быть явно доверен через TRUSTED_PROXY_IPS, backend закрыт сетью.
Settings скрывает значения входных данных в ошибках валидации.

Все POST-формы требуют csrf_input(request). Login rate limit использует общую БД.
Сессия истекает по idle-timeout. Фоновые запросы проверки, продления и снятия
блокировки IP проверяют сессию без продления её срока.
UserSession в БД обеспечивает отзыв logout и всех сессий при смене пароля/деактивации.
При HTTPS Secure cookie обязательна; не публикуйте HTTP-порт web в обход gateway.
HTTP в production явно разрешён через HTTP APP_PUBLIC_URL, OpenAPI и проверки
секретов сохраняются. Сертификаты не генерировать автоматически, монтировать только
пользовательские файлы read-only. Не показывать версию Nginx. Ссылки static должны
использовать origin-relative .path; проверять их загрузку через gateway.
После изменения Compose или .env используйте up -d, не start.

Sidebar сортирует CIDR численно. Папки закрыты при первом открытии, сохраняют
контекст выбранного проекта; / без выбранного проекта не открывает первый проект.
Таблица read-only, sticky header, pagination/filter через AJAX с защитой dirty drawer.
Задержавшийся AJAX-ответ не должен заменять более свежий.
Редактирование адреса использует возобновляемую блокировку на строке IP.
Запись из веб-интерфейса и интеграции проверяет её в транзакции с изменениями адреса.
Карточка передаёт идентификаторы и типы пользовательских столбцов. Если схема
изменилась, сохранение устаревшей формы отклоняется.

Ping: NoTest/OK/NO, первый job при создании, затем расписание. Ограничивайте общий
ICMP-параллелизм, особенно при нескольких worker. PostgreSQL advisory locks
защищают claim; SQLite рассчитан на одного worker. Ошибка запуска ping - не NO.
Не убирайте убийство/reap ping-процесса при timeout/cancellation.

## 14. Текущий статус

Две постоянные ветки: stable по умолчанию и dev для разработки.
Пользовательский запуск единый, без отдельного production deployment.
Схема БД и данные прежних установок сохраняются; используйте прежний Compose project.
Старые теги/образы остаются историей, они не нужны для установки из stable.

## 15. TODO

Мониторинг ping, optimistic locking IP-записей, preview импорта, browser-тесты,
UTC-aware даты, ротация API-токенов. Не внедряйте это без отдельной задачи.
