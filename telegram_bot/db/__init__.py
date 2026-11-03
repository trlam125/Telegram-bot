"""Single stable Supabase facade composed from domain-specific repositories."""
from telegram_bot.db.client import SupabaseClient
from telegram_bot.db.users import UserRepository
from telegram_bot.db.files import FileRepository
from telegram_bot.db.updates import UpdateRepository
from telegram_bot.db.notifications import NotificationRepository
from telegram_bot.db.gateway import GatewayRepository

class SupabaseDB(UserRepository, FileRepository, UpdateRepository,
                 NotificationRepository, GatewayRepository, SupabaseClient):
    """Keep all previous `supabase_db.method(...)` calls compatible."""

supabase_db = SupabaseDB()

__all__ = ['SupabaseDB','supabase_db']
