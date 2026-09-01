#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE="$ROOT_DIR/.env"
DOMAIN=${1:-rsc.example.com}

if [ -f "$ENV_FILE" ]; then
  printf 'env_exists=%s\n' "$ENV_FILE"
  exit 0
fi

POSTGRES_PASSWORD=$(openssl rand -hex 24)
MIGRATOR_PASSWORD=$(openssl rand -hex 24)
API_DATABASE_PASSWORD=$(openssl rand -hex 24)
BACKUP_DATABASE_PASSWORD=$(openssl rand -hex 24)
JWT_SECRET=$(openssl rand -hex 32)
TMP_FILE=$(mktemp)
trap 'rm -f "$TMP_FILE"' EXIT

umask 077
printf '%s\n' \
  "APP_DOMAIN=$DOMAIN" \
  "POSTGRES_DB=star_oam" \
  "POSTGRES_USER=star_oam_bootstrap" \
  "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" \
  "OAM_DB_MIGRATOR_PASSWORD=$MIGRATOR_PASSWORD" \
  "OAM_DB_API_PASSWORD=$API_DATABASE_PASSWORD" \
  "OAM_DB_BACKUP_PASSWORD=$BACKUP_DATABASE_PASSWORD" \
  "OAM_DATABASE_EXPECTED_RUNTIME_ROLE=star_oam_api" \
  "OAM_DATABASE_EXPECTED_MIGRATION_ROLE=star_oam_migrator" \
  "OAM_JWT_SECRET=$JWT_SECRET" \
  "OAM_DATABASE_SCHEMA_MODE=alembic" \
  "OAM_COOKIE_SECURE=true" \
  "OAM_PASSWORD_LOGIN_ENABLED=false" \
  "OAM_SMS_LOGIN_ENABLED=false" \
  "OAM_SMS_PROVIDER=disabled" \
  "OAM_SMS_ACCESS_KEY_ID=" \
  "OAM_SMS_ACCESS_KEY_SECRET=" \
  "OAM_SMS_SIGN_NAME=" \
  "OAM_SMS_TEMPLATE_CODE=" \
  "OAM_SMS_SCHEME_NAME=RSC个人仓登录" \
  "OAM_WECHAT_LOGIN_ENABLED=false" \
  "OAM_WECHAT_PROVIDER=disabled" \
  "OAM_WECHAT_APP_ID=" \
  "OAM_WECHAT_APP_SECRET=" > "$TMP_FILE"
install -m 600 "$TMP_FILE" "$ENV_FILE"
printf 'env_created=%s\n' "$ENV_FILE"
printf '%s\n' 'next_step=enable and fully configure SMS or WeChat login before starting production'
