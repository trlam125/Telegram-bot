"""Vercel FastAPI entrypoint.

Vercel auto-detects a FastAPI instance named ``app`` from ``app.py``.
The real application stays in ``web_app.py`` so local/other-host deployment
can keep using the same webhook implementation.
"""

from web_app import app

__all__ = ["app"]
