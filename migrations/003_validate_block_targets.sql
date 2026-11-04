-- Apply once after migration 002. Safe to execute again.
-- /block and /unblock must only operate on real users already stored in bot_users.
-- Remove phantom blocked IDs created by the previous unrestricted INSERT.
DELETE FROM public.bot_notification_access AS access
WHERE access.blocked IS TRUE
  AND NOT EXISTS (
      SELECT 1 FROM public.bot_users AS users
      WHERE users.telegram_id = access.telegram_id
  );

CREATE OR REPLACE FUNCTION public.bot_set_user_blocked(
    p_telegram_id bigint, p_blocked boolean
)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
SET search_path = public AS $$
BEGIN
    IF p_telegram_id IS NULL OR p_telegram_id <= 0 OR p_blocked IS NULL THEN
        RAISE EXCEPTION 'Invalid block arguments';
    END IF;

    -- Do not manufacture a notification_access row for an arbitrary ID.
    IF NOT EXISTS (
        SELECT 1 FROM public.bot_users WHERE telegram_id = p_telegram_id
    ) THEN
        RETURN FALSE;
    END IF;

    -- /unblock of someone who is not blocked is not a successful unblock.
    IF NOT p_blocked AND NOT EXISTS (
        SELECT 1 FROM public.bot_notification_access
        WHERE telegram_id = p_telegram_id AND blocked IS TRUE
    ) THEN
        RETURN FALSE;
    END IF;

    INSERT INTO public.bot_notification_access
        (telegram_id, subscription_epoch, blocked, updated_at)
    VALUES (p_telegram_id, 0, p_blocked, now())
    ON CONFLICT (telegram_id) DO UPDATE
        SET subscription_epoch = 0, blocked = EXCLUDED.blocked, updated_at = now();
    RETURN TRUE;
END;
$$;

REVOKE ALL ON FUNCTION public.bot_set_user_blocked(bigint, boolean)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.bot_set_user_blocked(bigint, boolean)
    TO service_role;
NOTIFY pgrst, 'reload schema';
