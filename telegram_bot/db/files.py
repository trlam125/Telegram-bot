"""Metadata about files archived in private Telegram storage."""
from typing import Optional


class FileRepository:
    async def save_file(
        self,
        *,
        telegram_user_id: int,
        storage_chat_id: int,
        storage_message_id: int,
        telegram_file_id: Optional[str],
        telegram_file_unique_id: Optional[str],
        file_name: Optional[str],
        mime_type: Optional[str],
        file_type: str,
        file_size: Optional[int],
        source_chat_id: Optional[int],
        source_message_id: Optional[int],
    ) -> None:
        payload = {
            "telegram_user_id": int(telegram_user_id),
            "storage_chat_id": int(storage_chat_id),
            "storage_message_id": int(storage_message_id),
            "telegram_file_id": telegram_file_id,
            "telegram_file_unique_id": telegram_file_unique_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "file_type": file_type,
            "file_size": int(file_size) if file_size is not None else None,
            "source_chat_id": int(source_chat_id) if source_chat_id is not None else None,
            "source_message_id": int(source_message_id) if source_message_id is not None else None,
        }
        response = await self._request(
            "POST",
            "bot_files",
            params={"on_conflict": "storage_chat_id,storage_message_id"},
            json_body=payload,
            prefer="resolution=merge-duplicates,return=minimal",
        )
        response.raise_for_status()
