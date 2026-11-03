# Supabase database setup

This is a **complete source project**, but database rows live in your existing Supabase project.
Do not delete your Supabase project or existing Telegram storage channel.

- Existing database: keep all tables and user data, and retain the same SUPABASE_URL and key.
- If `public.bot_broadcast_settings` already exists: **no SQL is needed**.
- If it is missing: run `001_bot_broadcast_settings.sql` in Supabase SQL Editor once.
  The SQL does not overwrite an existing `/notify_off` or `/notify_on` setting.
- Other existing tables used by the app are not recreated by this migration.
