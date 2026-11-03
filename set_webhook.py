"""Backward-compatible webhook setup command."""
from scripts.set_webhook import main
import asyncio

if __name__ == '__main__':
    asyncio.run(main())
