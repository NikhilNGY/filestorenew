import base64
import re
import asyncio
import urllib.parse
import aiohttp
import requests
import motor.motor_asyncio
from datetime import datetime, timedelta
from pyrogram import filters, Client, errors
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import (
    UserNotParticipant,
    Forbidden,
    PeerIdInvalid,
    ChatAdminRequired,
    FloodWait
)


# =============================================================== #
#                UNIFIED MONGODB & MULTI-DB ENGINE                #
# =============================================================== #

class MongoDB:
    _instances = {}

    def __new__(cls, uri: str, db_name: str):
        if (uri, db_name) not in cls._instances:
            instance = super().__new__(cls)
            instance.uri = uri
            instance.db_name = db_name
            instance.client = motor.motor_asyncio.AsyncIOMotorClient(uri)
            instance.db = instance.client[db_name]
            instance.user_data = instance.db["users"]
            instance.channel_data = instance.db["channels"]
            instance.premium_users = instance.db["pros"]
            instance.files_data = instance.db["files"]
            instance.fsub_status = instance.db["fsub_status"]
            instance.request_sub = instance.db["request_sub"]
            instance.worker_bots = instance.db["worker_bots"]

            # Unlimited Secondary MongoDB Connections for /datadb
            instance.secondary_clients = {}
            instance.multi_db_enabled = True
            instance._rr_index = 0

            cls._instances[(uri, db_name)] = instance
        return cls._instances[(uri, db_name)]

    # --------------------------------------------------------------- #
    #       /datadb MULTI-DATABASE CONNECTION & STORAGE METHODS       #
    # --------------------------------------------------------------- #

    def _connect_secondary_db(self, entry: dict):
        """Initialize a Motor client for a secondary MongoDB URL entry"""
        db_id = entry.get("id")
        url = entry.get("url")
        if not db_id or not url:
            return None
        if db_id not in self.secondary_clients:
            try:
                sec_client = motor.motor_asyncio.AsyncIOMotorClient(url)
                sec_db = sec_client[self.db_name]
                self.secondary_clients[db_id] = {
                    "id": db_id,
                    "name": entry.get("name", f"DB_{db_id}"),
                    "url": url,
                    "client": sec_client,
                    "db": sec_db,
                    "files": sec_db["files"],
                    "users": sec_db["users"]
                }
            except Exception as e:
                print(f"Failed to connect secondary DB ({entry.get('name')}): {e}")
                return None
        return self.secondary_clients.get(db_id)

    async def init_multi_db(self, bot_client=None):
        """Load saved /datadb settings and URLs from primary DB on startup"""
        status = await self.get_multi_db_status()
        urls = await self.get_db_urls()
        self.multi_db_enabled = status
        for entry in urls:
            self._connect_secondary_db(entry)
        if bot_client is not None:
            bot_client.multi_db_enabled = status
            bot_client.db_urls = urls
        return status, urls

    async def set_multi_db_status(self, enabled: bool):
        """Save /datadb On/Off status"""
        self.multi_db_enabled = bool(enabled)
        await self.user_data.update_one(
            {"_id": "multidb_config"},
            {"$set": {"enabled": self.multi_db_enabled}},
            upsert=True
        )

    async def get_multi_db_status(self) -> bool:
        """Get /datadb On/Off status"""
        doc = await self.user_data.find_one({"_id": "multidb_config"})
        if doc and "enabled" in doc:
            self.multi_db_enabled = bool(doc["enabled"])
        return self.multi_db_enabled

    async def get_db_urls(self) -> list[dict]:
        """Get all configured unlimited database URLs"""
        doc = await self.user_data.find_one({"_id": "multidb_config"})
        urls = doc.get("urls", []) if doc else []
        for entry in urls:
            self._connect_secondary_db(entry)
        return urls

    async def set_db_urls(self, urls: list[dict]):
        """Overwrite the full list of configured database URLs"""
        await self.user_data.update_one(
            {"_id": "multidb_config"},
            {"$set": {"urls": urls}},
            upsert=True
        )
        active_ids = {u["id"] for u in urls if "id" in u}
        for existing_id in list(self.secondary_clients.keys()):
            if existing_id not in active_ids:
                self.secondary_clients.pop(existing_id, None)
        for entry in urls:
            self._connect_secondary_db(entry)

    async def add_db_url(self, entry: dict):
        """Add a new MongoDB URL from /datadb and connect it immediately"""
        urls = await self.get_db_urls()
        if not any(u.get("url") == entry.get("url") for u in urls):
            urls.append(entry)
            await self.set_db_urls(urls)
        self._connect_secondary_db(entry)
        return True

    async def remove_db_url(self, db_id: str):
        """Remove a specific MongoDB URL by its short ID or name"""
        urls = await self.get_db_urls()
        updated_urls = [
            u for u in urls
            if u.get("id") != db_id and u.get("name") != db_id and u.get("url") != db_id
        ]
        await self.set_db_urls(updated_urls)
        self.secondary_clients.pop(db_id, None)
        return True

    async def clear_all_db_urls(self):
        """Remove all configured MongoDB URLs from /datadb"""
        await self.set_db_urls([])
        self.secondary_clients.clear()
        return True

    # --------------------------------------------------------------- #
    #       /bots & /otherbots MULTI-BOT MANAGEMENT METHODS           #
    # --------------------------------------------------------------- #

    async def get_setting(self, key: str, default=False):
        """Generic setting getter for multi_bot_enabled, auto_rotate_enabled, etc."""
        return await self.get_bot_setting(key, default)

    async def set_setting(self, key: str, value):
        """Generic setting setter for multi_bot_enabled, auto_rotate_enabled, etc."""
        await self.update_bot_setting(key, value)

    async def get_worker_bots(self) -> list[dict]:
        """Return list of all connected worker bots"""
        cursor = self.worker_bots.find({})
        bots = []
        async for doc in cursor:
            bots.append({
                "bot_id": doc.get("_id"),
                "token": doc.get("token"),
                "username": doc.get("username"),
                "name": doc.get("name", "Worker Bot"),
                "added_at": doc.get("added_at")
            })
        return bots

    async def add_worker_bot(self, bot_id: int, token: str, username: str, name: str = "Worker Bot"):
        """Add or update a worker bot in MongoDB"""
        await self.worker_bots.update_one(
            {"_id": int(bot_id)},
            {"$set": {
                "token": token,
                "username": username,
                "name": name,
                "added_at": datetime.now()
            }},
            upsert=True
        )
        return True

    async def remove_worker_bot(self, bot_id: int):
        """Remove a specific worker bot by bot_id"""
        await self.worker_bots.delete_one({"_id": int(bot_id)})
        return True

    async def clear_all_worker_bots(self):
        """Remove all worker bots"""
        await self.worker_bots.delete_many({})
        return True

    async def get_otherbots_config(self) -> dict:
        """Fetch /otherbots universal reply configuration"""
        doc = await self.user_data.find_one({"_id": "otherbots_config"})
        return doc.get("config", {}) if doc else {}

    async def update_otherbots_config(self, updates: dict):
        """Update /otherbots universal reply configuration"""
        current = await self.get_otherbots_config()
        current.update(updates)
        await self.user_data.update_one(
            {"_id": "otherbots_config"},
            {"$set": {"config": current}},
            upsert=True
        )
        return current

    # --------------------------------------------------------------- #
    #       FILE STORAGE ACROSS UNLIMITED DATABASES (/datadb)         #
    # --------------------------------------------------------------- #

    async def add_file(self, file_id: str, message_id: int, channel_id: int = None):
        """
        Save file mapping. When Multi-DB (/datadb) is ON and secondary URLs exist,
        distributes/replicates across configured databases automatically with fallback.
        """
        doc = {
            "_id": file_id,
            "message_id": message_id,
            "channel_id": channel_id,
            "created_at": datetime.now()
        }

        if self.multi_db_enabled and self.secondary_clients:
            sec_list = list(self.secondary_clients.values())
            target_sec = sec_list[self._rr_index % len(sec_list)]
            self._rr_index += 1
            try:
                await target_sec["files"].update_one(
                    {"_id": file_id},
                    {"$set": doc},
                    upsert=True
                )
            except Exception as e:
                print(f"Secondary DB write failed on {target_sec['name']}, falling back to primary: {e}")

        await self.files_data.update_one(
            {"_id": file_id},
            {"$set": doc},
            upsert=True
        )
        return True

    async def get_file(self, file_id: str) -> dict:
        """Retrieve file metadata from primary DB or any connected /datadb instance"""
        doc = await self.files_data.find_one({"_id": file_id})
        if doc:
            return doc

        if self.multi_db_enabled and self.secondary_clients:
            for sec in self.secondary_clients.values():
                try:
                    doc = await sec["files"].find_one({"_id": file_id})
                    if doc:
                        return doc
                except Exception:
                    continue
        return None

    # --------------------------------------------------------------- #
    #                 CHANNEL & USER MANAGEMENT                       #
    # --------------------------------------------------------------- #

    async def set_channels(self, channels: list[int]):
        await self.user_data.update_one(
            {"_id": 1},
            {"$set": {"channels": channels}},
            upsert=True
        )

    async def get_channels(self) -> list[int]:
        data = await self.user_data.find_one({"_id": 1})
        return data.get("channels", []) if data else []

    async def add_channel_user(self, channel_id: int, user_id: int):
        await self.channel_data.update_one(
            {"_id": channel_id},
            {"$addToSet": {"users": user_id}},
            upsert=True
        )

    async def remove_channel_user(self, channel_id: int, user_id: int):
        await self.channel_data.update_one(
            {"_id": channel_id},
            {"$pull": {"users": user_id}}
        )

    async def get_channel_users(self, channel_id: int) -> list[int]:
        doc = await self.channel_data.find_one({"_id": channel_id})
        return doc.get("users", []) if doc else []

    async def is_user_in_channel(self, channel_id: int, user_id: int) -> bool:
        doc = await self.channel_data.find_one(
            {"_id": channel_id, "users": {"$in": [user_id]}},
            {"_id": 1}
        )
        return doc is not None

    # --------------------------------------------------------------- #
    #                        PRO / PREMIUM USERS                      #
    # --------------------------------------------------------------- #

    async def add_pro(self, user_id: int, expiry_date: datetime = None):
        try:
            await self.premium_users.update_one(
                {"_id": user_id},
                {"$set": {"expiry_date": expiry_date}},
                upsert=True
            )
            return True
        except Exception as e:
            print(f"Failed to add premium user: {e}")
            return False

    async def remove_pro(self, user_id: int):
        try:
            await self.premium_users.delete_one({"_id": user_id})
            return True
        except Exception as e:
            print(f"Failed to remove premium user: {e}")
            return False

    async def is_pro(self, user_id: int):
        doc = await self.premium_users.find_one({"_id": user_id})
        if not doc:
            return False
        if "expiry_date" not in doc or doc["expiry_date"] is None:
            return True
        return doc["expiry_date"] > datetime.now()

    async def get_pros_list(self):
        current_time = datetime.now()
        cursor = self.premium_users.find({
            "$or": [
                {"expiry_date": None},
                {"expiry_date": {"$exists": False}},
                {"expiry_date": {"$gt": current_time}}
            ]
        })
        return [doc["_id"] async for doc in cursor]

    async def get_expiry_date(self, user_id: int) -> datetime:
        doc = await self.premium_users.find_one({"_id": user_id})
        return doc.get("expiry_date") if doc else None

    # --------------------------------------------------------------- #
    #                         USERBASE FUNCTIONS                      #
    # --------------------------------------------------------------- #

    async def present_user(self, user_id: int) -> bool:
        found = await self.user_data.find_one({"_id": user_id})
        if found:
            return True
        if self.multi_db_enabled and self.secondary_clients:
            for sec in self.secondary_clients.values():
                try:
                    if await sec["users"].find_one({"_id": user_id}):
                        return True
                except Exception:
                    continue
        return False

    async def add_user(self, user_id: int, ban: bool = False):
        await self.user_data.update_one(
            {"_id": user_id},
            {"$set": {"ban": ban}},
            upsert=True
        )
        if self.multi_db_enabled and self.secondary_clients:
            sec_list = list(self.secondary_clients.values())
            target_sec = sec_list[user_id % len(sec_list)]
            try:
                await target_sec["users"].update_one(
                    {"_id": user_id},
                    {"$set": {"ban": ban}},
                    upsert=True
                )
            except Exception:
                pass

    async def full_userbase(self) -> list[int]:
        """Return all valid integer user IDs across primary and secondary DBs"""
        user_ids = set()
        cursor = self.user_data.find()
        async for doc in cursor:
            if isinstance(doc.get("_id"), int) and doc.get("_id") != 1:
                user_ids.add(doc["_id"])

        if self.multi_db_enabled and self.secondary_clients:
            for sec in self.secondary_clients.values():
                try:
                    async for doc in sec["users"].find():
                        if isinstance(doc.get("_id"), int) and doc.get("_id") != 1:
                            user_ids.add(doc["_id"])
                except Exception:
                    continue
        return list(user_ids)

    async def del_user(self, user_id: int):
        await self.user_data.delete_one({"_id": user_id})
        if self.secondary_clients:
            for sec in self.secondary_clients.values():
                try:
                    await sec["users"].delete_one({"_id": user_id})
                except Exception:
                    continue

    async def ban_user(self, user_id: int):
        await self.user_data.update_one({"_id": user_id}, {"$set": {"ban": True}}, upsert=True)

    async def unban_user(self, user_id: int):
        await self.user_data.update_one({"_id": user_id}, {"$set": {"ban": False}}, upsert=True)

    async def is_banned(self, user_id: int) -> bool:
        user = await self.user_data.find_one({"_id": user_id})
        return user.get("ban", False) if user else False

    # --------------------------------------------------------------- #
    #                  FSUB CHANNELS & TOGGLE SETTINGS                #
    # --------------------------------------------------------------- #

    async def set_fsub_status(self, enabled: bool):
        await self.update_bot_setting("fsub_enabled", bool(enabled))

    async def get_fsub_status_global(self) -> bool:
        return await self.get_bot_setting("fsub_enabled", True)

    async def set_auto_rotate(self, enabled: bool):
        await self.update_bot_setting("auto_rotate", bool(enabled))

    async def get_auto_rotate(self) -> bool:
        return await self.get_bot_setting("auto_rotate", False)

    async def set_fsub_channels(self, fsub_data: dict):
        await self.user_data.update_one(
            {"_id": "fsub_channels"},
            {"$set": {"channels": fsub_data}},
            upsert=True
        )

    async def get_fsub_channels(self) -> dict:
        data = await self.user_data.find_one({"_id": "fsub_channels"})
        return data.get("channels", {}) if data else {}

    async def add_fsub_channel(self, channel_id: int, channel_data: list):
        current_data = await self.get_fsub_channels()
        current_data[str(channel_id)] = channel_data
        await self.set_fsub_channels(current_data)

    async def remove_fsub_channel(self, channel_id: int):
        current_data = await self.get_fsub_channels()
        current_data.pop(str(channel_id), None)
        await self.set_fsub_channels(current_data)

    # --------------------------------------------------------------- #
    #                    SHORTENER SETTINGS FUNCTIONS                 #
    # --------------------------------------------------------------- #

    async def set_shortner_settings(self, shortner_data: dict):
        await self.user_data.update_one(
            {"_id": "shortner_settings"},
            {"$set": {"settings": shortner_data}},
            upsert=True
        )

    async def get_shortner_settings(self) -> dict:
        data = await self.user_data.find_one({"_id": "shortner_settings"})
        return data.get("settings", {}) if data else {}

    async def update_shortner_setting(self, key: str, value):
        current_data = await self.get_shortner_settings()
        current_data[key] = value
        await self.set_shortner_settings(current_data)

    async def get_shortner_status(self) -> bool:
        settings = await self.get_shortner_settings()
        return settings.get("enabled", False)

    async def set_shortner_status(self, enabled: bool):
        await self.update_shortner_setting("enabled", enabled)

    # --------------------------------------------------------------- #
    #                  FSUB STATUS & JOIN REQUEST TRACKING            #
    # --------------------------------------------------------------- #

    async def update_fsub_status(self, user_id: int, channel_id: int, status: str):
        await self.fsub_status.update_one(
            {"user_id": user_id, "channel_id": channel_id},
            {"$set": {"status": status, "last_updated": datetime.now()}},
            upsert=True
        )

    async def get_fsub_status(self, user_id: int, channel_id: int) -> str:
        doc = await self.fsub_status.find_one({"user_id": user_id, "channel_id": channel_id})
        return doc.get("status") if doc else None

    async def remove_fsub_status(self, user_id: int, channel_id: int):
        await self.fsub_status.delete_one({"user_id": user_id, "channel_id": channel_id})

    async def get_user_fsub_statuses(self, user_id: int) -> dict:
        cursor = self.fsub_status.find({"user_id": user_id})
        statuses = {}
        async for doc in cursor:
            statuses[doc["channel_id"]] = doc["status"]
        return statuses

    async def clear_expired_fsub_statuses(self, days: int = 7):
        cutoff_date = datetime.now() - timedelta(days=days)
        await self.fsub_status.delete_many({"last_updated": {"$lt": cutoff_date}})

    async def add_join_request(self, user_id: int, channel_id: int, request_id: int = None):
        await self.request_sub.update_one(
            {"user_id": user_id, "channel_id": channel_id},
            {"$set": {
                "request_id": request_id,
                "status": "pending",
                "submitted_at": datetime.now(),
                "last_updated": datetime.now()
            }},
            upsert=True
        )

    async def update_join_request_status(self, user_id: int, channel_id: int, status: str):
        await self.request_sub.update_one(
            {"user_id": user_id, "channel_id": channel_id},
            {"$set": {"status": status, "last_updated": datetime.now()}}
        )

    async def get_join_request_status(self, user_id: int, channel_id: int) -> str:
        doc = await self.request_sub.find_one({"user_id": user_id, "channel_id": channel_id})
        return doc.get("status") if doc else None

    async def has_submitted_join_request(self, user_id: int, channel_id: int) -> bool:
        doc = await self.request_sub.find_one({"user_id": user_id, "channel_id": channel_id})
        return doc is not None

    async def remove_join_request(self, user_id: int, channel_id: int):
        await self.request_sub.delete_one({"user_id": user_id, "channel_id": channel_id})

    # --------------------------------------------------------------- #
    #                  DB CHANNELS MANAGEMENT FUNCTIONS               #
    # --------------------------------------------------------------- #

    async def set_db_channels(self, db_channels_data: dict):
        await self.user_data.update_one(
            {"_id": "db_channels"},
            {"$set": {"channels": db_channels_data}},
            upsert=True
        )

    async def get_db_channels(self) -> dict:
        data = await self.user_data.find_one({"_id": "db_channels"})
        return data.get("channels", {}) if data else {}

    async def add_db_channel(self, channel_id: int, channel_data: dict):
        current_data = await self.get_db_channels()
        current_data[str(channel_id)] = channel_data
        await self.set_db_channels(current_data)

    async def remove_db_channel(self, channel_id: int):
        current_data = await self.get_db_channels()
        current_data.pop(str(channel_id), None)
        await self.set_db_channels(current_data)

    async def update_db_channel(self, channel_id: int, channel_data: dict):
        current_data = await self.get_db_channels()
        if str(channel_id) in current_data:
            current_data[str(channel_id)].update(channel_data)
            await self.set_db_channels(current_data)

    async def get_primary_db_channel(self) -> int:
        db_channels = await self.get_db_channels()
        for channel_id_str, channel_data in db_channels.items():
            if channel_data.get("is_primary", False):
                return int(channel_id_str)
        return None

    async def set_primary_db_channel(self, channel_id: int):
        db_channels = await self.get_db_channels()
        for ch_id, ch_data in db_channels.items():
            ch_data["is_primary"] = False
        if str(channel_id) in db_channels:
            db_channels[str(channel_id)]["is_primary"] = True
        await self.set_db_channels(db_channels)

    async def get_active_db_channels(self) -> dict:
        db_channels = await self.get_db_channels()
        return {
            cid: cdata for cid, cdata in db_channels.items()
            if cdata.get("is_active", True)
        }

    async def toggle_db_channel_status(self, channel_id: int):
        db_channels = await self.get_db_channels()
        if str(channel_id) in db_channels:
            current_status = db_channels[str(channel_id)].get("is_active", True)
            db_channels[str(channel_id)]["is_active"] = not current_status
            await self.set_db_channels(db_channels)
            return not current_status
        return None

    # --------------------------------------------------------------- #
    #              BOT, MESSAGE & ADMIN SETTINGS FUNCTIONS            #
    # --------------------------------------------------------------- #

    async def set_bot_settings(self, settings_data: dict):
        await self.user_data.update_one(
            {"_id": "bot_settings"},
            {"$set": {"settings": settings_data}},
            upsert=True
        )

    async def get_bot_settings(self) -> dict:
        data = await self.user_data.find_one({"_id": "bot_settings"})
        return data.get("settings", {}) if data else {}

    async def update_bot_setting(self, key: str, value):
        current_data = await self.get_bot_settings()
        current_data[key] = value
        await self.set_bot_settings(current_data)

    async def get_bot_setting(self, key: str, default=None):
        settings = await self.get_bot_settings()
        return settings.get(key, default)

    async def set_messages_settings(self, messages_data: dict):
        await self.user_data.update_one(
            {"_id": "messages_settings"},
            {"$set": {"messages": messages_data}},
            upsert=True
        )

    async def get_messages_settings(self) -> dict:
        data = await self.user_data.find_one({"_id": "messages_settings"})
        return data.get("messages", {}) if data else {}

    async def update_message_setting(self, key: str, value: str):
        current_data = await self.get_messages_settings()
        current_data[key] = value
        await self.set_messages_settings(current_data)

    async def get_message_setting(self, key: str, default: str = ""):
        messages = await self.get_messages_settings()
        return messages.get(key, default)

    async def set_admins_list(self, admins_list: list):
        await self.user_data.update_one(
            {"_id": "admins_list"},
            {"$set": {"admins": admins_list}},
            upsert=True
        )

    async def get_admins_list(self) -> list:
        data = await self.user_data.find_one({"_id": "admins_list"})
        return data.get("admins", []) if data else []

    async def add_admin(self, admin_id: int):
        current_admins = await self.get_admins_list()
        if admin_id not in current_admins:
            current_admins.append(admin_id)
            await self.set_admins_list(current_admins)
            return True
        return False

    async def remove_admin(self, admin_id: int):
        current_admins = await self.get_admins_list()
        if admin_id in current_admins:
            current_admins.remove(admin_id)
            await self.set_admins_list(current_admins)
            return True
        return False


# =============================================================== #
#                ASYNC SHORTENER HELPER FUNCTION                  #
# =============================================================== #

async def get_short(url: str, client) -> str:
    """Non-blocking URL shortener using aiohttp (with fallback to requests)."""
    short_url = getattr(client, "short_url", None)
    short_api = getattr(client, "short_api", None)
    if not short_url or not short_api:
        return url
    try:
        clean_domain = short_url.replace("https://", "").replace("http://", "").strip("/")
        api_endpoint = f"https://{clean_domain}/api?api={short_api}&url={urllib.parse.quote(url)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_endpoint, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json(content_type=None)
                if data.get("status") == "success" or data.get("shortenedUrl"):
                    return data.get("shortenedUrl", url)
    except Exception as e:
        print(f"Shortener API error: {e}")
    return url


# =============================================================== #
#                ENCODING, DECODING & MESSAGE HELPERS             #
# =============================================================== #

async def encode(string: str) -> str:
    string_bytes = string.encode("ascii")
    base64_bytes = base64.urlsafe_b64encode(string_bytes)
    return base64_bytes.decode("ascii").strip("=")


async def decode(base64_string: str) -> str:
    base64_string = base64_string.strip("=")
    base64_bytes = (base64_string + "=" * (-len(base64_string) % 4)).encode("ascii")
    string_bytes = base64.urlsafe_b64decode(base64_bytes)
    return string_bytes.decode("ascii")


async def get_messages(client, message_ids):
    messages = []
    total_messages = 0
    while total_messages != len(message_ids):
        temb_ids = message_ids[total_messages:total_messages + 200]
        msgs = []
        try:
            msgs = await get_messages_from_db_channels(client, temb_ids)
        except FloodWait as e:
            wait_time = getattr(e, "value", getattr(e, "x", 5))
            await asyncio.sleep(wait_time)
            msgs = await get_messages_from_db_channels(client, temb_ids)
        except Exception:
            pass
        total_messages += len(temb_ids)
        messages.extend(msgs)
    return messages


async def get_message_id(client, message):
    """Get message ID and source channel ID from forwarded message or link"""
    if message.forward_from_chat:
        if message.forward_from_chat.id == client.db:
            return message.forward_from_message_id, client.db
        db_channels = getattr(client, "db_channels", {})
        for channel_id_str in db_channels.keys():
            if message.forward_from_chat.id == int(channel_id_str):
                return message.forward_from_message_id, int(channel_id_str)
        return 0, 0
    elif message.forward_sender_name:
        return 0, 0
    elif message.text:
        pattern = r"https://t.me/(?:c/)?(.*)/(\d+)"
        matches = re.match(pattern, message.text)
        if not matches:
            return 0, 0
        channel_id = matches.group(1)
        msg_id = int(matches.group(2))
        if channel_id.isdigit():
            if f"-100{channel_id}" == str(client.db):
                return msg_id, client.db
            db_channels = getattr(client, "db_channels", {})
            for channel_id_str in db_channels.keys():
                if f"-100{channel_id}" == channel_id_str:
                    return msg_id, int(channel_id_str)
        else:
            if hasattr(client, "db_channel") and channel_id == client.db_channel.username:
                return msg_id, client.db
            db_channels = getattr(client, "db_channels", {})
            for channel_id_str in db_channels.keys():
                try:
                    chat = await client.get_chat(int(channel_id_str))
                    if hasattr(chat, "username") and chat.username == channel_id:
                        return msg_id, int(channel_id_str)
                except Exception:
                    continue
    return 0, 0


async def get_messages_from_db_channels(client, temb_ids):
    """Get messages from multiple DB channels - tries primary first, then falls back to others"""
    messages = []
    try:
        primary_db = getattr(client, "primary_db_channel", client.db)
        msgs = await client.get_messages(chat_id=primary_db, message_ids=temb_ids)
        if not isinstance(msgs, list):
            msgs = [msgs]
        valid_msgs = [msg for msg in msgs if msg is not None and not getattr(msg, "empty", False)]
        messages.extend(valid_msgs)
        found_ids = {msg.id for msg in valid_msgs}
        missing_ids = [mid for mid in temb_ids if mid not in found_ids]

        if not missing_ids:
            return messages

        db_channels = getattr(client, "db_channels", {})
        for channel_id_str, channel_data in db_channels.items():
            if not channel_data.get("is_active", True):
                continue
            if int(channel_id_str) == primary_db:
                continue

            try:
                additional_msgs = await client.get_messages(
                    chat_id=int(channel_id_str),
                    message_ids=missing_ids
                )
                if not isinstance(additional_msgs, list):
                    additional_msgs = [additional_msgs]
                valid_additional = [msg for msg in additional_msgs if msg is not None and not getattr(msg, "empty", False)]
                messages.extend(valid_additional)

                found_additional_ids = {msg.id for msg in valid_additional}
                missing_ids = [mid for mid in missing_ids if mid not in found_additional_ids]

                if not missing_ids:
                    break
            except Exception as e:
                client.LOGGER(__name__, client.name).warning(f"Error getting messages from DB channel {channel_id_str}: {e}")
                continue

    except FloodWait as e:
        wait_time = getattr(e, "value", getattr(e, "x", 5))
        await asyncio.sleep(wait_time)
        return await get_messages_from_db_channels(client, temb_ids)
    except Exception as e:
        client.LOGGER(__name__, client.name).warning(f"Error getting messages from DB channels: {e}")

    return messages


# =============================================================== #
#                ADMIN & FORCE SUBSCRIBE CHECK HELPERS            #
# =============================================================== #

async def is_bot_admin(client, channel_id):
    try:
        bot = await client.get_chat_member(channel_id, "me")
        if bot.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            if bot.privileges:
                required_rights = ["can_invite_users", "can_delete_messages"]
                missing_rights = [right for right in required_rights if not getattr(bot.privileges, right, False)]
                if missing_rights:
                    return False, f"Bot is missing the following rights: {', '.join(missing_rights)}"
            return True, None
        return False, "Bot is not an admin in the channel."
    except errors.ChatAdminRequired:
        return False, "Bot lacks permission to access admin information in this channel."
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


async def check_subscription(client, user_id):
    """Enhanced subscription check with request channel and auto-rotate support."""
    if not getattr(client, "fsub_enabled", True):
        return {}

    statuses = {}
    if not await client.mongodb.present_user(user_id):
        await client.mongodb.add_user(user_id)

    for channel_id, (channel_name, channel_link, request, timer) in client.fsub_dict.items():
        try:
            user = await client.get_chat_member(channel_id, user_id)
            actual_status = user.status

            if actual_status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                await client.mongodb.update_fsub_status(user_id, channel_id, "joined")
                await client.mongodb.add_channel_user(channel_id, user_id)

                if request and await client.mongodb.has_submitted_join_request(user_id, channel_id):
                    await client.mongodb.update_join_request_status(user_id, channel_id, "approved")

                statuses[channel_id] = actual_status
                continue

            if request:
                has_request = await client.mongodb.has_submitted_join_request(user_id, channel_id)
                if has_request:
                    request_status = await client.mongodb.get_join_request_status(user_id, channel_id)
                    if request_status == "approved":
                        await client.mongodb.update_fsub_status(user_id, channel_id, "left")
                        await client.mongodb.remove_join_request(user_id, channel_id)
                        statuses[channel_id] = ChatMemberStatus.BANNED
                    else:
                        await client.mongodb.update_fsub_status(user_id, channel_id, "request_submitted")
                        statuses[channel_id] = ChatMemberStatus.MEMBER
                else:
                    await client.mongodb.update_fsub_status(user_id, channel_id, "not_requested")
                    statuses[channel_id] = ChatMemberStatus.BANNED
            else:
                await client.mongodb.update_fsub_status(user_id, channel_id, "left")
                await client.mongodb.remove_channel_user(channel_id, user_id)
                statuses[channel_id] = ChatMemberStatus.BANNED

        except UserNotParticipant:
            await client.mongodb.update_fsub_status(user_id, channel_id, "left")
            await client.mongodb.remove_channel_user(channel_id, user_id)

            if request:
                has_request = await client.mongodb.has_submitted_join_request(user_id, channel_id)
                if has_request:
                    await client.mongodb.update_fsub_status(user_id, channel_id, "request_submitted")
                    statuses[channel_id] = ChatMemberStatus.MEMBER
                else:
                    await client.mongodb.update_fsub_status(user_id, channel_id, "not_requested")
                    statuses[channel_id] = ChatMemberStatus.BANNED
            else:
                statuses[channel_id] = ChatMemberStatus.BANNED

        except Forbidden:
            client.LOGGER(__name__, client.name).warning(f"Bot lacks permission for {channel_name}.")
            statuses[channel_id] = None
        except Exception as e:
            client.LOGGER(__name__, client.name).warning(f"Error checking {channel_name}: {e}")
            statuses[channel_id] = None

    return statuses


def is_user_subscribed(statuses):
    """Check if user is subscribed to all channels."""
    return all(
        status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
        for status in statuses.values() if status is not None
    ) and bool(statuses)


def force_sub(func):
    """Decorator to enforce force subscription before executing a command."""
    async def wrapper(client: Client, message: Message):
        if not getattr(client, "fsub_enabled", True) or not getattr(client, "fsub_dict", {}):
            return await func(client, message)

        photo = client.messages.get("FSUB_PHOTO", "")
        if photo:
            msg = await message.reply_photo(caption="<b>ᴡᴀɪᴛ ᴀ sᴇᴄᴏɴᴅ.....</b>", photo=photo)
        else:
            msg = await message.reply("<code><b>ᴡᴀɪᴛ ᴀ sᴇᴄᴏɴᴅ.....</b></code>")

        user_id = message.from_user.id
        statuses = await check_subscription(client, user_id)

        if is_user_subscribed(statuses):
            await msg.delete()
            return await func(client, message)

        buttons = []
        channels_message = f"{client.messages.get('FSUB', 'Please join the required channels below to continue:')}\n\n"

        fsub_items = list(client.fsub_dict.items())
        if getattr(client, "auto_rotate", False) and len(fsub_items) > 1:
            offset = int(datetime.now().timestamp()) % len(fsub_items)
            fsub_items = fsub_items[offset:] + fsub_items[:offset]

        for channel_id, (channel_name, channel_link, request, timer) in fsub_items:
            status = statuses.get(channel_id, None)

            if timer > 0:
                expire_time = datetime.now() + timedelta(minutes=timer)
                try:
                    invite = await client.create_chat_invite_link(
                        chat_id=channel_id,
                        expire_date=expire_time,
                        creates_join_request=request
                    )
                    channel_link = invite.invite_link
                except Exception as e:
                    client.LOGGER(__name__, client.name).warning(f"Error creating invite link for {channel_name}: {e}")

            if status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                if request and await client.mongodb.has_submitted_join_request(user_id, channel_id):
                    request_status = await client.mongodb.get_join_request_status(user_id, channel_id)
                    if request_status == "pending":
                        continue
                if channel_link:
                    buttons.append(InlineKeyboardButton(channel_name, url=channel_link))

        from_link = message.text.split(" ")
        if len(from_link) > 1:
            try_again_link = f"https://t.me/{client.username}?start={from_link[1]}"
            buttons.append(InlineKeyboardButton("🔄 Try Again", url=try_again_link))

        buttons_markup = InlineKeyboardMarkup([[button] for button in buttons]) if buttons else None

        try:
            await msg.edit_text(text=channels_message, reply_markup=buttons_markup)
        except Exception as e:
            client.LOGGER(__name__, client.name).warning(f"Error updating force sub message: {e}")
            try:
                await msg.delete()
                await message.reply(text=channels_message, reply_markup=buttons_markup)
            except Exception:
                pass

    return wrapper


# =============================================================== #
#                    AUTO DELETE TIMER FUNCTIONS                  #
# =============================================================== #

DEL_MSG = """<b>This File is deleting automatically in <a href="https://t.me/{username}">{time}</a>.. Forward in your Saved Messages..!</b>"""


def convert_time(duration_seconds: int) -> str:
    periods = [
        ("Yᴇᴀʀ", 60 * 60 * 24 * 365),
        ("Mᴏɴᴛʜ", 60 * 60 * 24 * 30),
        ("Day", 60 * 60 * 24),
        ("Hour", 60 * 60),
        ("Minute", 60),
        ("Second", 1)
    ]

    parts = []
    for period_name, period_seconds in periods:
        if duration_seconds >= period_seconds:
            num_periods = duration_seconds // period_seconds
            duration_seconds %= period_seconds
            parts.append(f"{num_periods} {period_name}{'s' if num_periods > 1 else ''}")

    if len(parts) == 0:
        return "0 Sᴇᴄᴏɴᴅ"
    elif len(parts) == 1:
        return parts[0]
    else:
        return ", ".join(parts[:-1]) + " ᴀɴᴅ " + parts[-1]


async def batch_auto_del_notification(bot_username, messages, delay_time, transfer_link, chat_id, client):
    """Send one notification for batch of files and delete all after timer"""
    if not messages:
        return

    notification_msg = await client.send_message(
        chat_id=chat_id,
        text=DEL_MSG.format(username=bot_username, time=convert_time(delay_time)),
        disable_web_page_preview=True
    )

    await asyncio.sleep(delay_time)

    for msg in messages:
        try:
            await msg.delete()
        except Exception as e:
            print(f"Error deleting message {getattr(msg, 'id', 'Unknown')}: {e}")

    try:
        if transfer_link:
            name = "• ɢᴇᴛ ғɪʟᴇs •"
            link = f"https://t.me/{bot_username}?start={transfer_link}"
            button = [[
                InlineKeyboardButton(text=name, url=link),
                InlineKeyboardButton(text="ᴄʟᴏsᴇ •", callback_data="close")
            ]]
            await notification_msg.edit_text(
                text=f"<b>›› Pʀᴇᴠɪᴏᴜs Mᴇssᴀɢᴇ ᴡᴀs Dᴇʟᴇᴛᴇᴅ\n\nIғ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ɢᴇᴛ ᴛʜᴇ ғɪʟᴇs ᴀɢᴀɪɴ, ᴛʜᴇɴ ᴄʟɪᴄᴋ: <a href={link}>{name}</a> ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴇʟsᴇ ᴄʟᴏsᴇ ᴛʜɪs ᴍᴇssᴀɢᴇ.</b>",
                reply_markup=InlineKeyboardMarkup(button),
                disable_web_page_preview=True
            )
        else:
            await notification_msg.edit_text("<b>Pʀᴇᴠɪᴏᴜs Mᴇssᴀɢᴇ ᴡᴀs Dᴇʟᴇᴛᴇᴅ</b>")
    except Exception as e:
        print(f"Error updating notification message: {e}")
