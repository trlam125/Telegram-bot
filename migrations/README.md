# Supabase database setup

This is a **complete source project**, but database rows live in your existing Supabase project.
Do not delete your Supabase project or existing Telegram storage channel.

- Existing database: keep all tables and user data, and retain the same SUPABASE_URL and key.
- If `public.bot_broadcast_settings` already exists: **no SQL is needed**.
- If it is missing: run `001_bot_broadcast_settings.sql` in Supabase SQL Editor once.
  The SQL does not overwrite an existing `/notify_off` or `/notify_on` setting.
- Other existing tables used by the app are not recreated by this migration.

## 002: owner controls, reset and blocklist (required for this release)

After the existing 001 migration, run `002_owner_subscription_access.sql` in the
same Supabase project's SQL Editor, **before deploying the updated bot**.

- The migration adds a persistent permission/blocked table, generation counter,
  and three service-role-only RPC functions.
- Existing opt-ins are backfilled once. Running the migration again will not
  restore subscriptions revoked by `/reset_subscribers`.
- Do not delete `bot_users`, `bot_alert_subscribers`, or `bot_gateway_events`.
- The bot uses `SUPABASE_SECRET_KEY` or `SUPABASE_SERVICE_ROLE_KEY` on the server.
  Do not put these keys in a frontend or Telegram message.
- Verify `TELEGRAM_OWNER_ID` is **your personal user ID** (not the bot ID).
- If you deploy before running 002, normal chats fail closed during access
  checks, and notification delivery cannot load its control settings.
