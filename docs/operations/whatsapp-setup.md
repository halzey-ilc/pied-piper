# Запуск WhatsApp

Как подключить номер к CRM: Cloud API в режиме Coexistence, приложение WhatsApp
Business остаётся на телефоне. Описание API для фронтенда: [WHATSAPP_API.md](../WHATSAPP_API.md).

## 1. Что получить у партнёра Meta

Перед договором уточните у партнёра:

- поддерживается ли Coexistence для номера Кыргызстана (+996);
- приходят ли вебхуки в формате Cloud API с подписью `X-Hub-Signature-256`;
- работает ли отправка через `POST /<phone-number-id>/messages` с заголовком
  `Authorization: Bearer`.

Если формат другой, CRM нужно доработать под этого партнёра.

| Что нужно | Переменная |
|---|---|
| App Secret приложения Meta | `WHATSAPP_APP_SECRET` |
| Постоянный токен доступа | `WHATSAPP_ACCESS_TOKEN` |
| Phone Number ID | `WHATSAPP_PHONE_NUMBER_ID` |
| Адрес API, если партнёр даёт свой | `WHATSAPP_API_BASE_URL` |

## 2. Что подготовить самим

Токен проверки вебхука (любая случайная строка, минимум 50 символов):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Результат запишите в `WHATSAPP_VERIFY_TOKEN`.

Id сотрудника, на которого будут создаваться новые лиды:

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api python manage.py shell -c "from django.contrib.auth import get_user_model as U; print(U().objects.get(username='ЛОГИН').id)"
```

Результат запишите в `WHATSAPP_LEAD_OWNER_USER_ID`.

## 3. Настройки на сервере

Заполните в `.env` все пять переменных: `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`,
`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_LEAD_OWNER_USER_ID`.

В продакшене сервис не запустится, если заполнена только часть из них: ошибка назовёт,
чего не хватает. Если не заполнена ни одна, WhatsApp выключен, а отправка из CRM
возвращает ошибку `channel_not_configured`.

Токены нельзя хранить в git и пересылать в чатах. Вместо значения можно задать
`<ИМЯ>_FILE` с путём к файлу секрета.

## 4. Выкладка

```bash
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```

Сделайте резервную копию баз ([backup-restore.md](backup-restore.md)) и примените миграции.
Первая команда обновляет управляющую базу, вторая обновляет базы компаний, где лежат чаты:

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api python manage.py migrate --noinput
```

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api python manage.py migrate_tenants
```

## 5. Проверка токенов

Команда только читает данные у Meta и не отправляет сообщений:

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api python manage.py whatsapp_check
```

При успехе она покажет номер и название бизнеса.

## 6. Вебхук

В настройках Meta или партнёра укажите:

- **Callback URL:** `https://<домен>/api/v1/webhooks/whatsapp/<slug-компании>/`
- **Verify token:** значение `WHATSAPP_VERIFY_TOKEN`
- **Подписки:** `messages` и `smb_message_echoes`

## 7. Первая живая проверка

1. Напишите с другого телефона на рабочий номер: появился чат, создан лид с источником
   `whatsapp`.
2. Напишите с этого же телефона ещё раз: сообщение попало в тот же чат, второй лид не
   создан.
3. Ответьте из CRM: сообщение пришло на телефон.
4. Ответьте из приложения на телефоне: ответ появился в чате CRM.
5. Отправьте фото: в чате видна метка `[Фото]`.

Формат ответов с телефона (`smb_message_echoes`) не проверялся на живом аккаунте. Если
шаг 4 не сработал, сохраните пример вебхука из лога и передайте разработчику.

## Если что-то не работает

| Что видно | Причина |
|---|---|
| Meta не принимает Callback URL, ответ 403 | неверный `WHATSAPP_VERIFY_TOKEN` |
| Meta не принимает Callback URL, ответ 404 | неверный slug компании или токен не задан |
| Вебхуки получают 401 | неверный `WHATSAPP_APP_SECRET` |
| Вебхуки получают 503 | `WHATSAPP_APP_SECRET` не задан |
| Вебхуки получают 200, чатов нет | не запущен воркер (очередь `integrations`) |
| Чат есть, лида нет | не задан `WHATSAPP_LEAD_OWNER_USER_ID` или номер некорректный |
| Ответ из CRM даёт 409 `channel_delivery_failed` | WhatsApp отклонил запрос, запустите `whatsapp_check` |
| Сообщение стало `failed` с кодом `131047` | прошло больше 24 часов с сообщения клиента |

## Замена токенов

1. Выпустите новый токен у Meta или партнёра.
2. Обновите `.env` и перезапустите `api` и `worker`.
3. Запустите `whatsapp_check`.

После смены `WHATSAPP_VERIFY_TOKEN` обновите его и в настройках вебхука.
