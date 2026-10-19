import asyncio

import pyromod.listen
from pyrogram import idle

from bot import Bot, web_app
from config import (
    ADMINS,
    API_HASH,
    API_ID,
    AUTO_DEL,
    DB_CHANNEL,
    DB_NAME,
    DB_URI,
    DISABLE_BTN,
    FSUBS,
    MESSAGES,
    PROTECT,
    SESSION,
    TOKEN,
    WORKERS,
)


async def runner():
    # 1. Start the web server FIRST so deployment health checks on PORT 5010 pass immediately
    await web_app()

    # 2. Initialize and start the Bot client
    bot_client = Bot(
        SESSION,
        WORKERS,
        DB_CHANNEL,
        FSUBS,
        TOKEN,
        ADMINS,
        MESSAGES,
        AUTO_DEL,
        DB_URI,
        DB_NAME,
        API_ID,
        API_HASH,
        PROTECT,
        DISABLE_BTN,
    )

    await bot_client.start()
    await idle()
    await bot_client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(runner())
    except (KeyboardInterrupt, SystemExit):
        pass
