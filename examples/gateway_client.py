"""Drop-in client example for ANY other Python repository.

Only configure TELEGRAM_GATEWAY_URL and TELEGRAM_GATEWAY_KEY in that repo.
There is no Telegram bot token or chat ID in this file or project.
"""

import os

import httpx

GATEWAY_URL = os.environ["TELEGRAM_GATEWAY_URL"].rstrip("/")
GATEWAY_KEY = os.environ["TELEGRAM_GATEWAY_KEY"]


def notify(message: str, *, title: str = "Thông báo", level: str = "info", event_id: str | None = None) -> dict:
    """Send a broadcast notification to everyone who registered /start."""
    payload = {"message": message, "title": title, "level": level}
    if event_id is not None:
        payload["event_id"] = event_id

    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{GATEWAY_URL}/gateway/v1/notify",
            headers={"X-Gateway-Key": GATEWAY_KEY},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def notify_fall(camera: str, *, event_time: str | None = None, event_id: str | None = None) -> dict:
    payload = {"camera": camera}
    if event_time is not None:
        payload["event_time"] = event_time
    if event_id is not None:
        payload["event_id"] = event_id

    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{GATEWAY_URL}/gateway/v1/fall",
            headers={"X-Gateway-Key": GATEWAY_KEY},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


if __name__ == "__main__":
    print(notify("Test gateway từ project bên ngoài", title="Kiểm tra kết nối"))
