# Установка IPtable для эксплуатации

Это self-hosted приложение для внутренней сети/VPN. Публичный исходный код и
публичный Docker-образ не требуют открывать вашу инвентаризацию в интернет.

## Требования

- Linux x86_64 с Docker Engine и Docker Compose v2 с поддержкой `up --wait`.
- Сертификат для внутреннего DNS-имени от доверенного вашим клиентам CA.
- Доступ worker к нужным подсетям по ICMP; доступ сервера к registry.
- Python 3 только для генерации конфигурации (без установки Python-зависимостей).

Выберите версию в [GitHub Releases](https://github.com/lasnet/IPTable/releases).
Проверьте успешный workflow `Publish release image`; адрес образа имеет вид
`ghcr.io/lasnet/iptable:<version>`. Если package еще private, анонимный pull не работает:
владелец должен включить Public в Package settings перед публичным распространением.
Начальная публикация поддерживает `linux/amd64`; ARM64 пока не проверен.

## Новая установка

1. Скачайте исходный архив выбранного релизного тега. Из него для установки нужны
   `deploy/`, `scripts/` и эта инструкция. Сборка приложения на сервере не нужна.
2. Из корня распакованного архива выполните (выберите опубликованную версию):

```bash
python3 scripts/init_production.py --image ghcr.io/lasnet/iptable:v0.1.0-rc.1
```

Скрипт создаст `deploy/.env` с правами `0600`, независимыми случайными паролями
и ключом сессий. Существующий файл не перезаписывается. Образ можно закрепить
точным `@sha256:...` вместо тега. Не копируйте секреты из development.
Bootstrap-пароль администратора находится в этом приватном файле; не публикуйте его.

3. Поместите сертификат и ключ в `deploy/tls/fullchain.pem` и
   `deploy/tls/privkey.pem`. Каталог и ключ должны быть доступны proxy UID/GID 101:

```bash
sudo chown -R root:101 deploy/tls
sudo chmod 750 deploy/tls
sudo chmod 640 deploy/tls/privkey.pem
sudo chmod 644 deploy/tls/fullchain.pem
```

Используйте отдельную копию сертификата для этого приложения, не меняйте права
на общесистемное хранилище ключей. Для rootless Docker/SELinux адаптируйте права
и mount labels к вашей платформе. Не делайте приватный ключ world-readable.
Внутренний CA установите в доверенные хранилища браузеров.

4. В `deploy/.env` задайте `HTTPS_BIND_ADDRESS` как конкретный LAN/VPN IP сервера.
   По умолчанию это `127.0.0.1`: приложение недоступно с других машин.
   `HTTPS_PORT` по умолчанию 443. Ограничьте входящий доступ нужной сетью средствами
   firewall и VPN; не полагайтесь только на настройки Docker/UFW.
5. Запустите из корня архива:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yml pull
docker compose --env-file deploy/.env -f deploy/compose.yml up -d --wait
docker compose --env-file deploy/.env -f deploy/compose.yml ps -a
```

Откройте `https://ваше-внутреннее-имя`. В production всегда включены secure cookies,
security headers и запрет OpenAPI UI. HTTP не публикуется. PostgreSQL и backend
доступны только внутри Docker-сетей. Прокси заменяет клиентские forwarded headers,
поэтому login rate limit получает адрес клиента, а не подставленный им заголовок.
Не подключайте посторонние контейнеры к сетям приложения.

`migrate` должен завершиться с кодом 0, остальные сервисы должны работать.
Healthcheck worker проверяет БД, а не прогресс ping: отдельно проверьте его логи
и тестовую подсеть. Nginx ограничивает загрузку файла 3 MiB; приложение по умолчанию
2 MB. При увеличении `CSV_IMPORT_MAX_BYTES` согласуйте лимит в `deploy/nginx.conf`.
При обновлении сертификата перезапустите `proxy`.

## Настройки

В `deploy/.env.example` перечислены обязательные production-параметры. Дополнительные
настройки приложения (язык, интервалы, лимиты) можно добавить в `deploy/.env` по
корневому `.env.example`. Production Compose принудительно задает `APP_ENV=production`
и строит `DATABASE_URL` из production `POSTGRES_*`.
Пароль БД должен быть URL-safe; генератор использует hex. Изменение `.env` не меняет
пароль уже созданной БД или администратора. При обычном обновлении храните секреты.

## Backup и обновление

Все команды ниже используют **тот же** production Compose project. Не меняйте его
имя `iptable-production` без плана переноса данных. Оно определяет имена volumes.

```bash
export COMPOSE_FILE=deploy/compose.yml
export COMPOSE_ENV_FILES=deploy/.env
export BACKUP_DIR=backups/production
scripts/backup_postgres.sh
```

Храните зашифрованные копии вне сервера, ограничивайте права, проверяйте восстановление
на отдельном стенде. Новые dump-файлы создаются с приватными правами (`umask 077`);
права уже существующих файлов не меняются. Расписание/внешнее хранение настраивает оператор.

Обновление: прочитайте CHANGELOG и миграции, сделайте backup, измените только
`IPTABLE_IMAGE` в production `.env` на новую версию, затем:

```bash
docker compose pull
docker compose stop proxy web worker
docker compose run --rm migrate
docker compose up -d --wait
```

Продолжайте только если миграции завершились успешно. Для отката недостаточно поменять
тег, если изменилась схема БД: проверьте совместимость или восстановите backup с потерей
изменений после него. Инструкция восстановления: раздел README о PostgreSQL, с теми же
`COMPOSE_FILE`/`COMPOSE_ENV_FILES`, остановленными web/worker/proxy и явным подтверждением.

**Никогда не выполняйте `docker compose down -v` на production:** это удалит volume БД.
Не запускайте development-код против production БД.

## Переход с прежнего docker-compose.yml

Не заменяйте прежний stack без backup: новый Compose намеренно создает отдельную БД,
а не подключает старый volume. Остановите запись в старом stack, сделайте финальный
dump старым скриптом/контекстом. Запустите только `postgres` нового stack, восстановите
dump с production-контекстом, выполните `run --rm migrate`, затем `up -d --wait`.
Для старых данных сохраните ожидаемый `INITIAL_ADMIN_USERNAME`: изменение логина
переносит административную роль. Bootstrap-пароль не заменяет пароль из восстановленной БД.
Проверьте записи и права, только после этого переключайте пользователей. Старый volume
сохраняйте до подтвержденной проверки переноса; восстановление сначала отрепетируйте
на копии, не на единственном экземпляре данных.
