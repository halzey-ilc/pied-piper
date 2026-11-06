# WhatsApp: API для фронтенда

Что нужно для экрана чатов. Настройка сервера: [operations/whatsapp-setup.md](operations/whatsapp-setup.md).

Все запросы идут на `/api/v1/communications/` с заголовками `Authorization: Bearer <access>`
и `X-Tenant: <slug-компании>`. Схема всех полей есть в OpenAPI.

## Подключение номера

Вместо окна с инструкцией про токены нужен такой сценарий:

1. `GET whatsapp/status/`: если `connected: true`, кнопку подключения не показываем.
2. Если `configured: true` и `connected: false`, показываем QR-код из `GET whatsapp/qr/`.
3. Опрашиваем `whatsapp/qr/` раз в несколько секунд: QR-код меняется примерно каждые 20
   секунд, а после сканирования приходит `status: connected`.
4. Если `configured: false`, показываем «WhatsApp ещё не настроен на сервере».

`GET whatsapp/status/`:

| Поле | Значение |
|---|---|
| `provider` | `green_api` или `cloud_api` |
| `configured` | настроен ли канал на сервере для этой компании |
| `connected` | работает ли номер |
| `state` | `authorized`, `notAuthorized`, `starting`, `blocked`, `suspended`, `unavailable`, `not_configured` |

`state: blocked` или `suspended`: покажите заметное предупреждение, номер ограничен.

`GET whatsapp/qr/` нужен доступ `communications.connect` (директор и РОП), иначе вернётся 403.
Кто отсканирует код, тот подключит свой номер к компании, поэтому другим ролям QR не показываем.

| Поле | Значение |
|---|---|
| `status` | `qr`, `connected`, `unavailable` |
| `image` | готовая строка для `<img src>` при `status: qr`, иначе `null` |

## Как получать новые сообщения

Push-событий нет: фронтенд опрашивает API. Список чатов отсортирован по последней
активности, поэтому новые сообщения поднимают чат наверх. Открытый чат и список
достаточно опрашивать раз в несколько секунд.

## Запросы

| Запрос | Что делает |
|---|---|
| `GET conversations/` | список чатов: `{count, results}` |
| `GET conversations/{id}/` | один чат |
| `GET conversations/{id}/messages/` | сообщения, старые первыми: `{results}` |
| `POST conversations/{id}/messages/` | ответить, тело `{"text": "..."}` (до 4096 символов), ответ 201 |
| `POST conversations/{id}/assign/` | взять чат себе |
| `POST conversations/{id}/read/` | сбросить непрочитанные |
| `POST conversations/{id}/close/` | закрыть чат |
| `POST conversations/{id}/reopen/` | открыть снова |

Фильтры `GET conversations/`: `channel`, `status` (`open`, `closed`), `lead_id`, а также
`limit` (1–200, по умолчанию 50) и `offset`.

**Карточка лида:** `GET conversations/?lead_id=<id лида>` вернёт его чаты. Некорректный
`lead_id` вернёт 400.

`GET .../messages/` тоже принимает `limit` и `offset`. По умолчанию это первые 50 сообщений,
поэтому для длинных чатов нужна подгрузка.

## Чат

| Поле | Значение |
|---|---|
| `id` | id чата |
| `channel` | `whatsapp`, `telegram`, `instagram`, `email` |
| `external_contact_id` | для WhatsApp номер цифрами, без `+`; если клиент скрыл номер, `...@lid` |
| `contact_name` | имя из профиля WhatsApp, может быть `null` |
| `status` | `open` или `closed` |
| `lead_id` | связанный лид или `null` |
| `customer_id` | связанный клиент или `null` |
| `owner_user_id` | ответственный; `null` значит, что чат никто не взял |
| `unread_count` | непрочитанные входящие |
| `last_message_at` | время последнего сообщения |

Новый чат приходит без ответственного и виден всем, у кого есть доступ к чатам.

## Сообщение

| Поле | Значение |
|---|---|
| `id` | id сообщения |
| `direction` | `inbound` от клиента, `outbound` от бизнеса |
| `text` | текст |
| `status` | `received`, `sent`, `failed` (`pending` наружу обычно не попадает) |
| `failure_code` | код ошибки WhatsApp или `null` |
| `sender_user_id` | кто ответил из CRM |
| `created_at` | время |

`outbound` с `sender_user_id: null` значит, что ответ написан в приложении на телефоне.

Статус `sent` значит, что WhatsApp принял сообщение. Он не означает, что клиент его
получил: статусов «доставлено» и «прочитано» пока нет.

## Что показать пользователю

| Ситуация | Как определить | Что показать |
|---|---|---|
| Сообщение не доставлено | `status: failed` | пометка на сообщении |
| Прошло 24 часа (только Meta) | `failure_code: "131047"` | «Прошло больше 24 часов с сообщения клиента. Обычным сообщением ответить нельзя» |
| Нет WhatsApp у номера | `failure_code: "noAccount"` | «У этого номера нет WhatsApp» |
| Номер ограничен | `failure_code: "suspended"` | «Отправка временно ограничена» |
| WhatsApp отклонил отправку | 409, `channel_delivery_failed` | ошибка у поля ввода, текст не очищать |
| Канал не подключён | 409, `channel_not_configured` | «WhatsApp не подключён» |
| Чат закрыт | 409, `conversation_invalid_state` | предложить открыть чат |
| Нет прав на ответ | 403 | скрыть поле ответа |

Формат ошибки: `{"error": {"code": "...", "message": "...", "request_id": "..."}}`.

## Вложения

Green API: фото, видео, голосовые, документы и стикеры до 20 МБ сохраняются в базе компании.
У такого сообщения заполнено поле `attachment`, иначе оно `null`:

| Поле | Значение |
|---|---|
| `kind` | `image`, `video`, `audio`, `document` |
| `content_type` | тип файла; неизвестный отдаётся как `application/octet-stream` |
| `file_name` | имя файла |
| `size` | размер в байтах |

Сам файл: `GET conversations/{id}/messages/{message_id}/attachment/`, права те же, что на чат.
Файл скачивается сразу после сообщения, поэтому `attachment` может появиться чуть позже.

В `text` остаётся метка: `[Фото]`, `[Видео]`, `[Голосовое сообщение]`, `[Документ]`, `[Стикер]`,
`[Геолокация]`, `[Контакт]`, после метки идёт подпись, если она есть. Файлы больше 20 МБ и
вложения Meta Cloud API не скачиваются, для них видна только метка.

## Пока не реализовано

- скачивание вложений через Meta Cloud API;
- шаблоны для сообщений после окна 24 часа;
- статусы «доставлено» и «прочитано»;
- push-обновления.
