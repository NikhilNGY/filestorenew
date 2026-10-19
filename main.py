import asyncio
import pyromod.listen  # Ensures client.ask() and client.listen() work across all plugins
from pyrogram import compose
from bot import Bot, web_app
from config import (
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
    DISABLE_BTN
)


async def main():
    app = [
        Bot(
            session=SESSION,
            workers=WORKERS,
            db=DB_CHANNEL,
            fsub=FSUBS,
            token=TOKEN,
            admins=ADMINS,
            messages=MESSAGES,
            auto_del=AUTO_DEL,
            db_uri=DB_URI,
            db_name=DB_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            protect=PROTECT,
            disable_btn=DISABLE_BTN
        )
    ]
    await compose(app)


async def runner():
    await asyncio.gather(
        main(),
        web_app()
    )


if __name__ == "__main__":
    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
