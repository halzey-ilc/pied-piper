# API contract (backend)

Собственный контракт backend для frontend `crm_axlon`.
- **Часть 1** — то, что реализовано и уже согласовано с frontend (его `docs/API_CONTRACT.md`, часть 1).
- **Часть 2** — **предложение** для этапа P0 из [IMPLEMENTATION_AUDIT.md](IMPLEMENTATION_AUDIT.md).

Часть 2 реализована в этапах P0-2…P0-4 (ветки `feature/p0-2-deals`, `feature/p0-3-ops`,
`feature/p0-4-cabinet`) — полный перечень endpoints, миграций и открытых вопросов см.
[P0_IMPLEMENTATION_REPORT.md](P0_IMPLEMENTATION_REPORT.md). Точная схема реализованного API —
OpenAPI (`/api/docs/`, `manage.py spectacular`).

## Часть 1. Реализовано

| Правило | Значение |
|---|---|
| Префикс | `/api/v1/` |
| Авторизация | `Authorization: Bearer <access>` (SimpleJWT). `POST auth/token/` → `{access, refresh}`; `POST auth/token/refresh/` ротирует refresh, старый попадает в blacklist; `auth/logout/`, `auth/logout-all/` |
| Компания | Заголовок `X-Tenant: <slug>` из `GET me/` → `memberships[].tenant`. Нет членства → 404 |
| Формат | JSON, `snake_case` |
| Ошибки | `{"error": {"code", "message", "details"?, "request_id"}}`; 400 `validation_error` (details в формате DRF), 401, 403 `access_denied`, 404 `not_found`, 409 `conflict`, 429, 500 |
| Пагинация | `limit` 1–200 (50), `offset`; ответ `{count, results}`. Исключения, которые будут выровнены: `GET sales/pipelines/` (массив), `GET communications/conversations/{id}/messages/` (`{results}`) |
| Деньги | Десятичная строка `"1500.00"` + `currency` (ISO 4217) |
| Время | `*_at` — UTC ISO 8601, `*_date` — `YYYY-MM-DD` |
| Конкуренция | `version` в PATCH; несовпадение → 409 `conflict` |
| Удаление | Ключевые записи не удаляются: закрываются статусом |

## Часть 2. Предложение для P0

Общие правила для новых endpoints:
- `{count, results}`;
- фильтры через query-параметры;
- `ordering` со списком разрешённых полей;
- деньги строками;
- часовой пояс компании для «сегодня», просрочек и групп сроков;
- менеджеру (`sales_rep`) параметры «чей» (`manager_id`, `assignee_id`, `host_id`) не расширяют видимость.

### `GET me/` — расширение без удаления полей

```json
{
  "id": "…", "username": "…",
  "display_name": "…", "is_platform_admin": false, "must_change_password": false,
  "memberships": [{"tenant": "acme", "role": "head_of_sales",
                   "company": {"id": "…", "name": "…", "timezone": "Asia/Bishkek", "currency": "KGS"},
                   "department": {"id": "…", "name": "…"},
                   "permissions": ["deals.view", "calls.rating.view", "…"]}]
}
```

### Сотрудники и отделы

Совпадает с предложением frontend, «Сотрудники и аккаунты».

| Метод и путь | Права | Тело и ответ |
|---|---|---|
| `GET staff/` | director: компания; head_of_sales: свой отдел | `{count, results:[{id, login, full_name, role, department, status: active\|invited\|deactivated, telegram: linked\|not_linked, last_login, created_by, created_at}]}` |
| `GET staff/create-options/` | создающий | `{creatable_role, departments[], fixed_department, login_is_optional}` |
| `POST staff/` | по иерархии: platform_admin → director, director → head_of_sales, head_of_sales → manager | `{full_name, login?, phone?, department_id?}` → 201 `{member, temporary_password}`. Пароль показывается **один раз** |
| `POST staff/{id}/deactivate/` | director, head_of_sales (свой отдел) | `{reassign_to_user_id?}`; вход блокируется, история сохраняется |
| `POST staff/{id}/reset-password/` | те же | → `{temporary_password}` один раз; `must_change_password=true` |
| `POST auth/password/change/` | сам пользователь | `{current_password, new_password}` → 204 |
| `POST me/telegram/link/` | сам пользователь | → `{code, expires_at}`; привязка подтверждается ботом по коду |
| `GET/POST departments/`, `PATCH departments/{id}/` | director | `{name, head_user_id}` |

### Сделки и воронки

| Метод и путь | Изменение |
|---|---|
| `GET sales/pipelines/` | `{count, results:[{id, name, is_default, stages:[{id, name, position, kind: open\|won\|lost, is_active}]}]}` |
| `PATCH sales/pipelines/{id}/`, `POST sales/pipelines/{id}/stages/`, `PATCH sales/stages/{id}/` | Настройка (director или head_of_sales с правом); этапы деактивируются, не удаляются |
| `GET sales/loss-reasons/`, `POST`, `PATCH {id}/` | Справочник причин отказа |
| `GET sales/deals/` | + `search`, `manager_id`, `customer_id`, `created_from/to`, `attention=overdue\|no_next_step`, `ordering` (`-updated_at`, `amount`, `next_action_at`, `title`) |
| `POST sales/deals/{id}/stage/` | `{stage_id, loss_reason_id?, comment?}`: lost-этап требует причину, если это включено |
| `GET sales/deals/{id}/stage-history/` | `{count, results:[{from_stage, to_stage, author, at, reason, version}]}` |
| `GET/POST sales/custom-fields/` | Определения: `{key, label, type: text\|number\|date\|datetime\|boolean\|single_select\|multi_select\|money\|url, options?, required, is_active}` |

### Клиенты и лиды

- `GET customers/?search=`: имя, email, телефон (по цифрам).
- `GET leads/{id}/duplicates/`: кандидаты по нормализованным телефону и email; автоматического слияния нет.
- `POST leads/{id}/convert/` `{customer_id?, create_deal?: {pipeline_id, title, amount}}`.

### Задачи и встречи

- Задача: `{task_type, priority: low|normal|high|urgent, status: open|completed|cancelled, assignee, created_by, due_at, is_overdue, due_group, result, subject: lead|customer|deal}`.
  `POST activities/{id}/complete/` принимает `{result}`.
- Встреча: `{status: scheduled|held|no_show|rescheduled|cancelled, confirmed_at, confirmed_by, host, booked_by, format: office|online|visit, duration_minutes}`.
  `POST meetings/{id}/outcome/` принимает `{outcome: held|no_show|rescheduled|cancelled, new_start?, comment}`.
  Итог создаёт или предлагает следующий шаг по правилу компании.

### Оплаты, планы, отсутствия

- `GET/POST payments/` `{deal_id, customer_id, amount, currency, kind: prepayment|additional|full, paid_on, seller_user_id, is_refund}`.
- `GET/PUT plans/` `{scope: company|department|user, scope_id, month, metric, target}` с историей изменений.
- `GET/POST absences/` `{user_id, kind: vacation|sick|day_off|business_trip, start_date, end_date, comment}`.

Кабинет РОПа (`team/today/`, `team/plan/`, `team/inactivity/`, …) — контракт этапа P0-4.
Он появится после «TZ_Kabinet_ROPa.docx» и «Книги определений метрик».
