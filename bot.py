import sys
from datetime import datetime

from aiohttp import web
from pyrogram import Client

from config import LOGGER, OWNER_ID, PORT, SHORT_API, SHORT_TUT, SHORT_URL
from database.database import MongoDB

try:
    from plugins import web_server
except ImportError:
    from __init__ import web_server

version = "v2.0.0"


class Bot(Client):
    def __init__(
        self,
        session,
        workers,
        db,
        fsub,
        token,
        admins,
        messages,
        auto_del,
        db_uri,
        db_name,
        api_id,
        api_hash,
        protect,
        disable_btn,
    ):
        super().__init__(
            name=session,
            api_hash=api_hash,
            api_id=api_id,
            plugins={"root": "plugins"},
            workers=workers,
            bot_token=token,
        )
        self.LOGGER = LOGGER
        self.name = session
        self.db = db
        self.fsub = fsub
        self.owner = OWNER_ID
        self.fsub_dict = {}
        self.admins = (
            list(set(admins + [OWNER_ID])) if OWNER_ID not in admins else list(admins)
        )
        self.messages = dict(messages)
        self.auto_del = auto_del
        self.protect = protect
        self.req_fsub = {}
        self.disable_btn = disable_btn
        self.reply_text = self.messages.get(
            "REPLY", "Do not send any useless message in the bot."
        )
        self.mongodb = MongoDB(db_uri, db_name)
        self.req_channels = []
        self.db_channels = {}
        self.primary_db_channel = db

        # Dynamic feature attributes (populated from config.py & MongoDB)
        self.fsub_enabled = True
        self.auto_rotate = False
        self.multi_db_enabled = True
        self.db_urls = []
        self.shortner_enabled = bool(SHORT_URL and SHORT_API)
        self.short_url = SHORT_URL or ""
        self.short_api = SHORT_API or ""
        self.tutorial_link = SHORT_TUT or "https://t.me/How_to_Download_7x/26"
        self.worker_clients = {}

    async def start(self):
        await super().start()
        usr_bot_me = await self.get_me()
        self.uptime = datetime.now()
        self.username = usr_bot_me.username

        # 1. Initialize Multi-Database (/datadb)
        try:
            await self.mongodb.init_multi_db(self)
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(
                f"Error initializing Multi-DB: {e}"
            )

        # 2. Load Persistent Bot Settings, Messages, Shortener & Admins
        try:
            saved_admins = await self.mongodb.get_admins_list()
            for admin_id in saved_admins:
                if admin_id not in self.admins:
                    self.admins.append(admin_id)

            saved_messages = await self.mongodb.get_messages_settings()
            if saved_messages:
                self.messages.update(saved_messages)
                if "REPLY" in saved_messages and saved_messages["REPLY"]:
                    self.reply_text = saved_messages["REPLY"]

            bot_settings = await self.mongodb.get_bot_settings()
            if "auto_del" in bot_settings:
                self.auto_del = int(bot_settings["auto_del"])
            if "protect" in bot_settings:
                self.protect = bool(bot_settings["protect"])
            if "disable_btn" in bot_settings:
                self.disable_btn = bool(bot_settings["disable_btn"])
            if "fsub_enabled" in bot_settings:
                self.fsub_enabled = bool(bot_settings["fsub_enabled"])
            if "auto_rotate" in bot_settings:
                self.auto_rotate = bool(bot_settings["auto_rotate"])
            if "reply_text" in bot_settings and bot_settings["reply_text"]:
                self.reply_text = bot_settings["reply_text"]

            shortner_cfg = await self.mongodb.get_shortner_settings()
            if shortner_cfg:
                self.shortner_enabled = shortner_cfg.get(
                    "enabled", self.shortner_enabled
                )
                self.short_url = shortner_cfg.get("short_url", self.short_url)
                self.short_api = shortner_cfg.get("short_api", self.short_api)
                self.tutorial_link = shortner_cfg.get(
                    "tutorial_link", self.tutorial_link
                )
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(
                f"Error loading saved settings: {e}"
            )

        # 3. Load Static FSub Channels from config.py
        if len(self.fsub) > 0:
            for channel in self.fsub:
                try:
                    channel_id = int(channel[0])
                    req_enabled = bool(channel[1])
                    timer_val = int(channel[2]) if len(channel) > 2 else 0

                    chat = await self.get_chat(channel_id)
                    name = chat.title
                    link = None

                    if not req_enabled:
                        link = chat.invite_link
                    if not link and timer_val <= 0:
                        chat_link = await self.create_chat_invite_link(
                            channel_id, creates_join_request=req_enabled
                        )
                        link = chat_link.invite_link

                    if timer_val > 0:
                        self.fsub_dict[channel_id] = [
                            name,
                            None,
                            req_enabled,
                            timer_val,
                        ]
                    else:
                        self.fsub_dict[channel_id] = [name, link, req_enabled, 0]

                    if req_enabled and channel_id not in self.req_channels:
                        self.req_channels.append(channel_id)
                except Exception as e:
                    self.LOGGER(__name__, self.name).warning(
                        f"Bot can't export invite link from Force Sub Channel: {e}"
                    )

        # 4. Load Dynamically Added FSub Channels from Database
        try:
            db_fsub_channels = await self.mongodb.get_fsub_channels()
            for channel_id_raw, channel_data in db_fsub_channels.items():
                channel_id = int(channel_id_raw)
                if channel_id in self.fsub_dict:
                    continue
                try:
                    chat = await self.get_chat(channel_id)
                    channel_data[0] = chat.title
                    self.fsub_dict[channel_id] = channel_data
                    if (
                        len(channel_data) > 2
                        and channel_data[2]
                        and channel_id not in self.req_channels
                    ):
                        self.req_channels.append(channel_id)
                except Exception as e:
                    self.LOGGER(__name__, self.name).warning(
                        f"Could not load dynamic fsub channel {channel_id}: {e}"
                    )
                    await self.mongodb.remove_fsub_channel(channel_id)
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(
                f"Error loading dynamic fsub channels: {e}"
            )

        await self.mongodb.set_channels(self.req_channels)

        # 5. Load DB Channels from Database
        try:
            db_channels_data = await self.mongodb.get_db_channels()
            self.db_channels = {}
            self.primary_db_channel = self.db

            for channel_id_str, channel_data in db_channels_data.items():
                channel_id = int(channel_id_str)
                try:
                    chat = await self.get_chat(channel_id)
                    channel_data["name"] = chat.title
                    self.db_channels[str(channel_id)] = channel_data

                    if channel_data.get("is_primary", False):
                        self.primary_db_channel = channel_id
                        self.db = channel_id
                except Exception as e:
                    self.LOGGER(__name__, self.name).warning(
                        f"Could not load DB channel {channel_id}: {e}"
                    )
                    await self.mongodb.remove_db_channel(channel_id)
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(f"Error loading DB channels: {e}")

        # 6. Verify Primary DB Channel Access
        try:
            db_channel = await self.get_chat(self.db)
            self.db_channel = db_channel
            test = await self.send_message(
                chat_id=db_channel.id, text="Testing DB Channel Access..."
            )
            await test.delete()

            self.LOGGER(__name__, self.name).info(
                f"Primary DB Channel: {self.primary_db_channel}"
            )
            self.LOGGER(__name__, self.name).info(
                f"Total DB Channels: {len(self.db_channels)}"
            )
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(e)
            self.LOGGER(__name__, self.name).warning(
                f"Make sure bot is Admin in DB Channel and double check DB_CHANNEL value (Current: {self.db})"
            )
            sys.exit(1)

        # 7. Start Connected Worker Bots (/bots) if any exist
        try:
            from plugins.others import start_all_worker_bots

            await start_all_worker_bots(self)
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(f"Worker bots startup notice: {e}")

        self.LOGGER(__name__, self.name).info(
            f"Bot @{self.username} Started Successfully ({version})!!"
        )

        try:
            restart_message = "<b>›› ʜᴇʏ sᴇɴᴘᴀɪ!!\n ɪ'ᴍ ᴀʟɪᴠᴇ ɴᴏᴡ 🍃...</b>"
            await self.send_message(chat_id=self.owner, text=restart_message)
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(
                f"Failed to send restart notification to owner: {e}"
            )

    async def stop(self, *args):
        for w_id, w_client in list(self.worker_clients.items()):
            try:
                await w_client.stop()
            except Exception:
                pass
        self.worker_clients.clear()
        await super().stop()
        self.LOGGER(__name__, self.name).info("Bot stopped.")


async def web_app():
    app = web.AppRunner(await web_server())
    await app.setup()
    bind_address = "0.0.0.0"
    await web.TCPSite(app, bind_address, int(PORT)).start()
