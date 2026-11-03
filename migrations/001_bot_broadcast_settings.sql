-- Run in Supabase SQL Editor once if bot_broadcast_settings does not exist.
-- Safe to repeat: ON CONFLICT preserves the current /notify_on or /notify_off state.
CREATE TABLE IF NOT EXISTS public.bot_broadcast_settings (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO public.bot_broadcast_settings (id, enabled)
VALUES (1, true)
ON CONFLICT (id) DO NOTHING;

ALTER TABLE public.bot_broadcast_settings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.bot_broadcast_settings FROM anon, authenticated;
GRANT SELECT, UPDATE ON TABLE public.bot_broadcast_settings TO service_role;
