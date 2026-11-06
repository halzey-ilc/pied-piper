#!/usr/bin/env bash
# Run the CRM with WhatsApp (Green API) on a PC with no public server.
#
#   ./scripts/setup_whatsapp_pc.sh
#
# Asks only for what it does not know yet, so it is safe to run again. Run it again
# after a reboot too: the tunnel address changes and the webhook must follow it.
# Secret values are never printed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml:compose.whatsapp-tunnel.yaml}"

fail() { echo "ОШИБКА: $1" >&2; exit 1; }

command -v docker >/dev/null || fail "Docker не установлен."
docker info >/dev/null 2>&1 || fail "Docker не запущен. Запустите Docker Desktop и повторите."
command -v python3 >/dev/null || fail "Нужен python3."

[ -f .env ] || ./scripts/init_env.sh

env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }

env_set() {
    KEY="$1" VALUE="$2" python3 - <<'PY'
import os, pathlib
key, value = os.environ["KEY"], os.environ["VALUE"]
path = pathlib.Path(".env")
lines = path.read_text().splitlines()
found = False
for index, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[index], found = f"{key}={value}", True
if not found:
    lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + "\n")
PY
}

ask() {  # ask KEY "question": keeps an existing value
    local value
    if [ -z "$(env_get "$1")" ]; then
        read -rp "$2: " value
        [ -n "$value" ] || fail "Значение не может быть пустым."
        env_set "$1" "$value"
    fi
}

ask_secret() {  # same, without showing what is typed or pasted
    local value
    if [ -z "$(env_get "$1")" ]; then
        read -rsp "$2 (при вставке символы не видны): " value
        echo
        [ -n "$value" ] || fail "Значение не может быть пустым."
        env_set "$1" "$value"
    fi
}

echo "== Данные Green API (console.green-api.com, страница инстанса) =="
ask GREEN_API_URL "Вставьте apiUrl"
ask GREEN_API_INSTANCE_ID "Вставьте idInstance"
ask_secret GREEN_API_TOKEN "Вставьте apiTokenInstance"

env_set WHATSAPP_PROVIDER green_api
if [ -z "$(env_get GREEN_API_WEBHOOK_TOKEN)" ]; then
    env_set GREEN_API_WEBHOOK_TOKEN "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
fi
if [ -z "$(env_get WHATSAPP_TENANT_SLUG)" ]; then
    read -rp "Короткое имя компании в CRM, латиницей [demo]: " slug
    env_set WHATSAPP_TENANT_SLUG "${slug:-demo}"
fi
chmod 600 .env
SLUG="$(env_get WHATSAPP_TENANT_SLUG)"

echo "== Запуск CRM (первый раз может занять несколько минут) =="
docker compose up -d --build --wait api worker beat </dev/null

# Without this, docker would swallow what the person types next.
manage() { docker compose exec -T "$@" </dev/null; }

echo "== Компания =="
manage -e TENANT_PROVISIONING_ENABLED=true api \
    python manage.py provision_tenant "$SLUG" --name "$SLUG" --create-organization \
    2>&1 | grep -E '^Tenant|^Created|Error' || fail "Не удалось создать компанию."

if [ -z "$(env_get WHATSAPP_LEAD_OWNER_USER_ID)" ]; then
    echo "== Сотрудник, на которого будут создаваться лиды =="
    read -rp "Логин сотрудника в CRM: " owner
    [ -n "$owner" ] || fail "Логин не может быть пустым."
    read -rsp "Пароль (нужен, только если такого сотрудника ещё нет; символы не видны): " password
    echo
    owner_id="$(manage -e OWNER="$owner" -e OWNER_PASSWORD="$password" api python manage.py shell -c "
import os
from django.contrib.auth import get_user_model
User = get_user_model()
user = User.objects.using('default').filter(username=os.environ['OWNER']).first()
if user is None:
    if not os.environ['OWNER_PASSWORD']:
        raise SystemExit('no such user and no password given')
    user = User(username=os.environ['OWNER'])
    user.set_password(os.environ['OWNER_PASSWORD'])
    user.save(using='default')
print('OWNER_ID=' + str(user.id))
" 2>/dev/null | sed -n 's/^OWNER_ID=//p')"
    [ -n "$owner_id" ] || fail "Не удалось найти или создать сотрудника."
    env_set WHATSAPP_LEAD_OWNER_USER_ID "$owner_id"
    manage -e TENANT_PROVISIONING_ENABLED=true api \
        python manage.py provision_tenant "$SLUG" --owner-username "$owner" 2>&1 \
        | grep -E '^Granted' || fail "Не удалось выдать сотруднику доступ."
fi

echo "== Применяем настройки =="
docker compose up -d --force-recreate --wait api worker </dev/null
manage api python manage.py migrate_tenants 2>&1 | tail -1

echo "== Открываем адрес для Green API =="
docker compose up -d --force-recreate webhook-proxy tunnel </dev/null
url=""
for _ in $(seq 1 45); do
    url="$(docker compose logs tunnel 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)"
    [ -n "$url" ] && break
    sleep 2
done
[ -n "$url" ] || fail "Туннель не открылся. Проверьте интернет и повторите."

sleep 5  # a new tunnel address takes a moment to start answering
manage api python manage.py whatsapp_check --public-url "$url"

cat <<'DONE'

Готово. Осталось:
  1. Подождать до 5 минут: инстанс Green API перезагружается.
  2. Если номер ещё не привязан, отсканировать QR-код в кабинете Green API:
     WhatsApp Business -> Настройки -> Связанные устройства -> Привязать устройство.
  3. Написать с другого телефона на рабочий номер.

Компьютер должен оставаться включённым. После перезагрузки запустите этот скрипт снова.
DONE
