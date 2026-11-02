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

## Часть 3. Добавлено 01–02.11.2026 (ветка `feature/deal-detail-embeds`)

Подробные схемы — в OpenAPI. Здесь — правила, которых из схемы не видно.

**Платформа** (`platform/…`, только `is_platform_admin`, без `X-Tenant`):
- `POST companies/{id}/directors/{user_id}/credentials/` → `{login, temporary_password}` — новый временный пароль директору (сброс или потерянные первичные данные). Старый пароль, refresh-токены и ссылки сброса перестают действовать; при входе требуется смена пароля. Пароль показывается один раз, в аудите — только факт `platform.director_credentials_issued`. Не активный директор этой компании → 404.
- Компании, подписки, модули, платежи за подписки, ошибки, обращения — см. OpenAPI (`platform_*`).

**Сотрудники**:
- РОП создаёт (`POST staff/`) и отключает (`POST staff/{id}/deactivate/`) менеджеров (`sales_rep`, `lead_caller`) **своего** отдела напрямую; переданный отдел игнорируется; РОПов, директора и чужие отделы — 403/404. `create-options` отдаёт `can_create_directly`; `mode` остаётся подсказкой интерфейсу (`request` для РОПа). Заявки (`staff/requests/`) сохранены.
- **Один активный РОП на отдел** (ограничение БД): второй при создании, смене роли или переводе → 409 `department_has_head` «…создайте отдельный отдел». Миграция `organizations.0004` останавливается с перечнем отделов, если нарушение уже есть в данных (данные не меняет).
- Сброс пароля сотрудника и деактивация отзывают refresh-токены и ссылки сброса. Уже выданный access-токен после **сброса пароля** действует до конца своего срока (`JWT_ACCESS_TTL_MINUTES`, по умолчанию 10 мин) — свойство JWT; после **деактивации** он отклоняется сразу (401).
- Деактивация (директор, РОП для своего отдела, одобренная заявка): открытые задачи **и открытые сделки** переходят к тому, кто отключил (по заявке — к РОПу-автору; если он неактивен — к директору); закрытые сделки, клиенты, заметки, звонки, встречи и аудит не меняются; у каждой переданной сделки — запись `deal.reassigned`. Получатель без отдела (директор) не меняет отдел записей — РОП отдела их по-прежнему видит. Передача — последний шаг операции и идемпотентна: при сбое фиксации учётной записи повтор операции доводит её до конца.

**Лиды**: `GET leads/?status&search&owner_id&source&created_from&created_to&ordering=name|created_at` (даты — дни компании, включительно); `POST leads/{id}/assign/ {owner_user_id}` (менеджер — нет, РОП — в отделе, директор — в компании); `GET leads/{id}/history/` (действия и имена полей); `POST leads/{id}/convert/ {customer_id?, create_deal?}` — `customer_id` привязывает к существующему видимому неархивному клиенту без создания нового; сделка создаётся в той же транзакции; повторный вызов возвращает первый результат и сделку не создаёт.

**Задачи**: `GET activities/?kind=task&assignee_id&priority&due_from&due_to&overdue=true&no_due_date=true`. **Встречи**: `GET meetings/?owner_id&date_from&date_to` (дни компании).

**Продажи и новые клиенты — единый источник** (`payments/infrastructure/sales.py`, решение владельца от 02.11.2026), одинаково для `payments/metrics/`, `team/plan-fact/` и `plans/progress/`:
- **продажа** — сделка, чей первый платёж (без возвратов) пришёлся на период; засчитывается продавцу этого платежа по реестру. Каждая новая сделка — отдельная продажа, даже у клиента, который уже покупал. Несколько (частичных) платежей одной сделки — одна продажа в месяце первого; полностью возвращённая сделка (сумма платежей с учётом возвратов ≤ 0) — не продажа; проигранная или переоткрытая сделка с невозвращёнными деньгами остаётся продажей и не считается дважды; платёж без сделки — деньги, но не продажа;
- **новые клиенты** (`new_clients`) — клиенты, чей первый платёж за всё время пришёлся на период, каждый один раз (клиент платежа = его клиент, иначе клиент сделки, иначе сама сделка). Отдельная метрика, с продажами не смешивается;
- `turnover` — сумма проданных сделок; `received` — деньги за период с вычетом возвратов; `debt` — остаток по проданным сделкам;
- `GET payments/metrics/?paid_from&paid_to&group_by=seller` — за любой день, неделю или месяц; с `group_by=seller` — `by_seller: [{seller_user_id, sales_count, new_clients}]`;
- KPI `deals_won` в `analytics/kpis/` — сделки, переведённые в статус «выиграна» (этап воронки), это **не** продажи по реестру платежей.

**Планы**: `GET plans/progress/` — плюс `remaining` (цель − факт, не ниже 0) и при `?daily=true` — `daily: [{date, actual}]` нарастающим итогом до сегодня.

**Лиды и клиенты в цифрах**: `GET analytics/kpis/crm-counts/?date_from&date_to&group_by=company|department|user` → `leads_total`, `leads_new`, `customers_total`, `customers_active`, `customers_new` (раздельно, без суммирования); охват по роли: директор — компания, РОП — свои записи и отдела, менеджер — свои. Входит в модуль тарифа `analytics`.

**Рабочее время** (`work-sessions/…`, `X-Tenant`): `POST start/` (201; уже открыта → 409), `POST stop/` (длительность; нет открытой → 409), `GET current/` → `{session|null}`, `GET ?user_id&status=open|closed|unconfirmed&date_from&date_to` — свои; РОП — свой отдел; директор — компания; чужой id → 403. Сессия — не вход в систему. Не остановленная за 16 ч сессия становится `unconfirmed`: конец неизвестен, `ended_at`/`duration_seconds` = null, в отработанное время не входит (почасовая задача `reports.settle_stale_work_sessions` и проверка при новом старте). В БД — не больше одной открытой сессии на сотрудника.
