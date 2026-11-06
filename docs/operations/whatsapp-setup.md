# Запуск WhatsApp

Сейчас номер подключается через Green API по QR-коду. Это временное решение до кнопки
Meta. Описание API для фронтенда: [WHATSAPP_API.md](../WHATSAPP_API.md).

## Green API: запуск по QR-коду

**Риск.** Green API неофициальный шлюз. Сам сервис предупреждает, что всегда есть риск
потерять WhatsApp на этом номере. Поэтому через него нельзя делать рассылки и массово
писать незнакомым людям. Если `whatsapp_check` покажет `blocked` или `suspended`,
остановите отправку и проверьте номер в приложении.

### 1. Аккаунт

Зарегистрируйтесь на [console.green-api.com](https://console.green-api.com) и создайте
инстанс. Бесплатный тариф Developer ограничен тремя чатами, для лидов нужен платный.
Скопируйте из консоли `apiUrl`, `idInstance` и `apiTokenInstance`.

### 2. Настройки на сервере

Создайте токен вебхука:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Заполните `.env`:

| Переменная | Значение |
|---|---|
| `WHATSAPP_PROVIDER` | `green_api` |
| `GREEN_API_URL` | `apiUrl` |
| `GREEN_API_INSTANCE_ID` | `idInstance` |
| `GREEN_API_TOKEN` | `apiTokenInstance` |
| `GREEN_API_WEBHOOK_TOKEN` | токен из команды выше |
| `WHATSAPP_TENANT_SLUG` | slug компании, которой принадлежит номер |
| `WHATSAPP_LEAD_OWNER_USER_ID` | id сотрудника для новых лидов (команда ниже) |

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api python manage.py shell -c "from django.contrib.auth import get_user_model as U; print(U().objects.get(username='ЛОГИН').id)"
```

Токены нельзя хранить в git и пересылать в чатах.

### 3. Выкладка

Сделайте резервную копию баз ([backup-restore.md](backup-restore.md)), затем:

```bash
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api python manage.py migrate --noinput
```

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api python manage.py migrate_tenants
```

### 4. Вебхук

Команда сама настроит Green API на вашу CRM. Инстанс перезагрузится, настройки
применяются до 5 минут:

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api python manage.py whatsapp_check --public-url https://ваш-домен
```

### 5. QR-код

На телефоне откройте WhatsApp Business: **Настройки → Связанные устройства → Привязать
устройство**. Отсканируйте QR-код из консоли Green API или с экрана CRM, когда фронтенд
подключит `GET whatsapp/qr/`. Приложение на телефоне продолжит работать.

### 6. Проверка

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api python manage.py whatsapp_check
```

Команда должна ответить `The number is connected.` Затем:

1. Напишите с другого телефона на рабочий номер: появился чат и лид.
2. Ответьте из CRM: сообщение пришло на телефон.
3. Ответьте из приложения на телефоне: ответ появился в чате CRM.

Формат вебхуков взят из документации Green API, на живом аккаунте он не проверялся.

### Если что-то не работает

| Что видно | Причина |
|---|---|
| Сервис не стартует, в ошибке названа переменная | не заполнена настройка |
| `whatsapp_check`: `Green API did not answer` | неверные URL, id инстанса или токен |
| `whatsapp_check`: `scan the QR code` | номер не привязан или сессия слетела |
| Вебхуки получают 401 | изменился `GREEN_API_WEBHOOK_TOKEN`: запустите шаг 4 заново |
| Вебхуки получают 200, чатов нет | не запущен воркер (очередь `integrations`) |
| Чат есть, лида нет | у клиента скрыт номер (`@lid`) или не задан владелец лидов |
| Сообщение `failed` с кодом `noAccount` | у номера нет WhatsApp |
| Сообщение `failed` с кодом `suspended` | Green API временно ограничил инстанс |

## Переход на кнопку Meta

Когда будет готов официальный канал:

1. Заполните настройки Cloud API из раздела ниже и задайте `WHATSAPP_PROVIDER=cloud_api`.
2. Перезапустите `api` и `worker`.
3. Отвяжите устройство Green API в приложении и остановите инстанс.

Чаты, лиды и история сохранятся: канал остаётся `whatsapp`, номера те же. Чаты с
контактами `@lid` останутся в истории, но ответить на них через Meta не получится.

# Запуск через Meta (Cloud API)

Cloud API в режиме Coexistence, приложение WhatsApp Business остаётся на телефоне.

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

Заполните в `.env` все шесть переменных: `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`,
`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_LEAD_OWNER_USER_ID`,
`WHATSAPP_TENANT_SLUG`.

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
