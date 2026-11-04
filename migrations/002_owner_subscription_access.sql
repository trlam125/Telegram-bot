-- Execute after 001_bot_broadcast_settings.sql. Safe for repeated executions.
-- Existing subscribers remain active until the owner explicitly resets them.
ALTER TABLE public.bot_broadcast_settings
    ADD COLUMN IF NOT EXISTS subscription_epoch bigint NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS public.bot_notification_access (
    telegram_id bigint PRIMARY KEY CHECK (telegram_id > 0),
    subscription_epoch bigint NOT NULL DEFAULT 0,
    blocked boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS bot_notification_access_active_idx
    ON public.bot_notification_access (subscription_epoch, telegram_id)
    WHERE blocked = false;

-- Bootstrap existing subscriptions only on the first migration. On later
-- re-runs, a user's revoked consent must never be re-enabled automatically.
-- Separate marker stored in a one-row table makes the migration idempotent.
CREATE TABLE IF NOT EXISTS public.bot_access_migrations (
    key text PRIMARY KEY,
    completed_at timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.bot_access_migrations
        WHERE key = '002_subscription_backfill'
    ) THEN
        INSERT INTO public.bot_notification_access (telegram_id, subscription_epoch)
        SELECT telegram_id, (SELECT subscription_epoch FROM public.bot_broadcast_settings WHERE id = 1)
        FROM public.bot_alert_subscribers WHERE enabled IS TRUE
        ON CONFLICT (telegram_id) DO NOTHING;

        INSERT INTO public.bot_notification_access (telegram_id, subscription_epoch)
        SELECT telegram_id, (SELECT subscription_epoch FROM public.bot_broadcast_settings WHERE id = 1)
        FROM public.bot_users WHERE weather_daily_enabled IS TRUE
        ON CONFLICT (telegram_id) DO NOTHING;

        INSERT INTO public.bot_access_migrations (key)
        VALUES ('002_subscription_backfill');
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION public.bot_reset_all_subscriptions()
RETURNS bigint LANGUAGE plpgsql SECURITY INVOKER
SET search_path = public AS $$
DECLARE new_epoch bigint;
BEGIN
    UPDATE public.bot_broadcast_settings
    SET subscription_epoch = subscription_epoch + 1, updated_at = now()
    WHERE id = 1
    RETURNING subscription_epoch INTO new_epoch;
    IF new_epoch IS NULL THEN
        RAISE EXCEPTION 'Missing bot_broadcast_settings row';
    END IF;
    RETURN new_epoch;
END;
$$;

CREATE OR REPLACE FUNCTION public.bot_activate_subscription(p_telegram_id bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
SET search_path = public AS $$
DECLARE activated boolean;
BEGIN
    IF p_telegram_id IS NULL OR p_telegram_id <= 0 THEN
        RAISE EXCEPTION 'Invalid Telegram user ID';
    END IF;
    INSERT INTO public.bot_notification_access
        (telegram_id, subscription_epoch, blocked, updated_at)
    SELECT p_telegram_id, subscription_epoch, false, now()
    FROM public.bot_broadcast_settings WHERE id = 1
    ON CONFLICT (telegram_id) DO UPDATE
        SET subscription_epoch = EXCLUDED.subscription_epoch, updated_at = now()
        WHERE NOT bot_notification_access.blocked
    RETURNING true INTO activated;
    RETURN COALESCE(activated, false);
END;
$$;

CREATE OR REPLACE FUNCTION public.bot_set_user_blocked(
    p_telegram_id bigint, p_blocked boolean
)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
SET search_path = public AS $$
BEGIN
    IF p_telegram_id IS NULL OR p_telegram_id <= 0 OR p_blocked IS NULL THEN
        RAISE EXCEPTION 'Invalid block arguments';
    END IF;
    INSERT INTO public.bot_notification_access
        (telegram_id, subscription_epoch, blocked, updated_at)
    VALUES (p_telegram_id, 0, p_blocked, now())
    ON CONFLICT (telegram_id) DO UPDATE
        SET subscription_epoch = 0, blocked = EXCLUDED.blocked, updated_at = now();
    RETURN true;
END;
$$;

ALTER TABLE public.bot_notification_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bot_access_migrations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.bot_notification_access, public.bot_access_migrations
    FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.bot_notification_access TO service_role;
REVOKE ALL ON FUNCTION public.bot_reset_all_subscriptions() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.bot_activate_subscription(bigint) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.bot_set_user_blocked(bigint, boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.bot_reset_all_subscriptions() TO service_role;
GRANT EXECUTE ON FUNCTION public.bot_activate_subscription(bigint) TO service_role;
GRANT EXECUTE ON FUNCTION public.bot_set_user_blocked(bigint, boolean) TO service_role;

-- Refresh PostgREST RPC metadata after new functions are installed.
NOTIFY pgrst, 'reload schema';
