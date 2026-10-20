import asyncio
import sys
from collections import defaultdict
from datetime import datetime

from aiohttp import web
from pyrogram import Client
from pyrogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

try:
    import pyromod.listen  # noqa: F401
    from pyromod.listen import ListenerTypes
except ImportError:
    ListenerTypes = None

from config import LOGGER, OWNER_ID, PORT, SHORT_API, SHORT_TUT, SHORT_URL
from database.database import MongoDB

try:
    from plugins import web_server
except ImportError:
    from __init__ import web_server

version = "v2.1.0"


# ============================================================================ #
# BULLETPROOF PYROMOD + PYROFORK PATCH (Fixes KeyError: ListenerTypes.MESSAGE) #
# ============================================================================ #
def _ensure_listeners(client_obj):
    """Guarantees client.listeners is always a defaultdict(list) even if pyromod resets it."""
    existing = getattr(client_obj, "__dict__", {}).get("listeners", None)
    new_listeners = defaultdict(list)
    if isinstance(existing, dict):
        for k, v in existing.items():
            new_listeners[k] = list(v) if isinstance(v, (list, tuple)) else []
    if ListenerTypes is not None:
        for l_type in ListenerTypes:
            if l_type not in new_listeners:
                new_listeners[l_type] = []
    client_obj.__dict__["listeners"] = new_listeners
    return new_listeners


_orig_client_init = Client.__init__


def _patched_client_init(self, *args, **kwargs):
    _orig_client_init(self, *args, **kwargs)
    _ensure_listeners(self)


Client.__init__ = _patched_client_init

if hasattr(Client, "get_listener_matching_with_data"):
    _orig_get_listener = Client.get_listener_matching_with_data

    def _safe_get_listener(self, data, listener_type):
        _ensure_listeners(self)
        return _orig_get_listener(self, data, listener_type)

    Client.get_listener_matching_with_data = _safe_get_listener


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
            in_memory=True,
        )

        _ensure_listeners(self)

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

        # Dynamic feature attributes (seeded from config.py defaults)
        self.fsub_enabled = True
        self.auto_rotate = False
        self.multi_db_enabled = True
        self.db_urls = []
        self.shortner_enabled = bool(SHORT_URL and SHORT_API)
        self.short_url = SHORT_URL or ""
        self.short_api = SHORT_API or ""
        self.tutorial_link = SHORT_TUT or "https://t.me/How_to_Download_7x/26"
        self.worker_clients = {}

    async def _register_bot_commands(self):
        """Registers the Telegram '/' command menu in the background without slowing startup."""
        user_commands = [
            BotCommand("start", "🚀 Start the bot & get files"),
            BotCommand("profile", "👤 View your subscription plan"),
            BotCommand("request", "📩 Request content (Premium)"),
        ]
        admin_commands = user_commands + [
            BotCommand("settings", "⚙️ Open unified bot settings panel"),
            BotCommand("forcesub", "📢 Manage Force Subscribe channels"),
            BotCommand("db", "📂 Manage Telegram DB channels"),
            BotCommand("datadb", "🗄️ Manage Multi-Database (MongoDB URLs)"),
            BotCommand("bots", "🤖 Manage delivery Worker Bots"),
            BotCommand("otherbots", "💬 Configure Worker Bots universal reply"),
            BotCommand("genlink", "🔗 Generate link for a single post"),
            BotCommand("batch", "📦 Generate batch link for multiple posts"),
            BotCommand("nbatch", "🔢 Generate batch link by count"),
            BotCommand("broadcast", "📣 Broadcast message to all users"),
            BotCommand("pbroadcast", "📌 Broadcast & pin message to all users"),
            BotCommand("stats", "📊 View system & bot usage statistics"),
            BotCommand("users", "👥 View total registered users count"),
            BotCommand("ban", "🚫 Ban user(s) by ID"),
            BotCommand("unban", "✅ Unban user(s) by ID"),
            BotCommand("addpremium", "💎 Grant Premium access to a user"),
            BotCommand("delpremium", "❌ Revoke Premium access from a user"),
            BotCommand("premiumusers", "📋 List all active Premium users"),
        ]

        try:
            await self.set_bot_commands(
                user_commands, scope=BotCommandScopeAllPrivateChats()
            )
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(
                f"Could not set default user commands: {e}"
            )

        await asyncio.gather(
            *(
                self.set_bot_commands(
                    admin_commands, scope=BotCommandScopeChat(chat_id=int(admin_id))
                )
                for admin_id in set(self.admins + [self.owner])
            ),
            return_exceptions=True,
        )

    async def _load_static_fsub_channel(self, channel):
        """Helper to load a single static FSub channel concurrently."""
        try:
            chat = await self.get_chat(channel[0])
            name = chat.title
            link = None
            if not channel[1]:
                link = chat.invite_link
            if not link and not channel[2]:
                chat_link = await self.create_chat_invite_link(
                    channel[0], creates_join_request=channel[1]
                )
                link = chat_link.invite_link
            if not channel[1]:
                self.fsub_dict[channel[0]] = [name, link, False, 0]
            if channel[1]:
                self.fsub_dict[channel[0]] = [name, link, True, 0]
                if channel[0] not in self.req_channels:
                    self.req_channels.append(channel[0])
            if channel[2] > 0:
                self.fsub_dict[channel[0]] = [name, None, channel[1], channel[2]]
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(
                f"Bot can't Export Invite link from Force Sub Channel {channel[0]}: {e}"
            )

    async def start(self):
        await super().start()
        _ensure_listeners(self)

        usr_bot_me = await self.get_me()
        self.uptime = datetime.now()
        self.username = usr_bot_me.username

        # 1. Fetch all MongoDB settings concurrently for ultra-fast startup
        try:
            (
                _,
                saved_admins,
                saved_messages,
                bot_settings,
                shortner_cfg,
                db_fsub_channels,
                db_channels_data,
            ) = await asyncio.gather(
                self.mongodb.init_multi_db(self),
                self.mongodb.get_admins_list(),
                self.mongodb.get_messages_settings(),
                self.mongodb.get_bot_settings(),
                self.mongodb.get_shortner_settings(),
                self.mongodb.get_fsub_channels(),
                self.mongodb.get_db_channels(),
                return_exceptions=True,
            )

            if isinstance(saved_admins, list):
                for admin_id in saved_admins:
                    if admin_id not in self.admins:
                        self.admins.append(admin_id)

            if isinstance(saved_messages, dict) and saved_messages:
                self.messages.update(saved_messages)
                if "REPLY" in saved_messages:
                    self.reply_text = saved_messages["REPLY"]

            if isinstance(bot_settings, dict):
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
                    self.messages["REPLY"] = bot_settings["reply_text"]

            if isinstance(shortner_cfg, dict) and shortner_cfg:
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
            db_fsub_channels = {}
            db_channels_data = {}

        # 2. Load Static FSub Channels Concurrently
        if self.fsub:
            await asyncio.gather(
                *(self._load_static_fsub_channel(ch) for ch in self.fsub),
                return_exceptions=True,
            )

        # 3. Load Dynamically Added FSub Channels from Database
        if isinstance(db_fsub_channels, dict):
            for channel_id_raw, channel_data in db_fsub_channels.items():
                channel_id = int(channel_id_raw)
                if channel_id in self.fsub_dict:
                    continue
                try:
                    chat = await self.get_chat(channel_id)
                    channel_data[0] = chat.title
                    self.fsub_dict[channel_id] = channel_data
                    if channel_data[2] and channel_id not in self.req_channels:
                        self.req_channels.append(channel_id)
                except Exception as e:
                    self.LOGGER(__name__, self.name).warning(
                        f"Could not load dynamic fsub channel {channel_id}: {e}"
                    )
                    await self.mongodb.remove_fsub_channel(channel_id)

        await self.mongodb.set_channels(self.req_channels)

        # 4. Load DB Channels from Database
        self.db_channels = {}
        self.primary_db_channel = self.db
        if isinstance(db_channels_data, dict):
            for channel_id_str, channel_data in db_channels_data.items():
                channel_id = int(channel_id_str)
                try:
                    chat = await self.get_chat(channel_id)
                    channel_data["name"] = chat.title
                    self.db_channels[channel_id_str] = channel_data

                    if channel_data.get("is_primary", False):
                        self.primary_db_channel = channel_id
                        self.db = channel_id
                except Exception as e:
                    self.LOGGER(__name__, self.name).warning(
                        f"Could not load DB channel {channel_id}: {e}"
                    )
                    await self.mongodb.remove_db_channel(channel_id)

        # 5. Verify Primary DB Channel Access
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

        # 6. Start Worker Bots & Register Commands in Background (Non-Blocking Startup!)
        async def _post_startup_tasks():
            try:
                from plugins.others import start_all_worker_bots

                await start_all_worker_bots(self)
            except Exception as e:
                self.LOGGER(__name__, self.name).warning(
                    f"Worker bots startup notice: {e}"
                )
            await self._register_bot_commands()

        asyncio.create_task(_post_startup_tasks())

        self.LOGGER(__name__, self.name).info("Bot Started Successfully!!")

        try:
            restart_message = "<b>›› ʜᴇʏ sᴇɴᴘᴀɪ!!\n ɪ'ᴍ ᴀʟɪᴠᴇ ɴᴏᴡ 🍃...</b>"
            await self.send_message(chat_id=self.owner, text=restart_message)
        except Exception as e:
            self.LOGGER(__name__, self.name).warning(
                f"Failed to send restart notification to owner: {e}"
            )

    async def stop(self, *args):
        if self.worker_clients:
            await asyncio.gather(
                *(w.stop() for w in list(self.worker_clients.values())),
                return_exceptions=True,
            )
        await super().stop()
        self.LOGGER(__name__, self.name).info("Bot stopped.")


async def web_app():
    app = web.AppRunner(await web_server())
    await app.setup()
    bind_address = "0.0.0.0"
    site = web.TCPSite(app, bind_address, int(PORT))
    await site.start()
