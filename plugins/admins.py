import asyncio
import secrets
import shutil
import string
from datetime import datetime

import psutil
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import (
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    UserIsBlocked,
)
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import OWNER_ID
from database.database import encode, get_message_id
from plugins.start import (
    ListenerTimeout,
    cancel_user_listeners,
    has_active_listener,
    is_admin,
    safe_edit_or_send,
    wait_for_user_input,
)

# =============================================================== #
#                ADMIN SETTINGS & SYSTEM STATS                    #
# =============================================================== #


@Client.on_callback_query(filters.regex("^admins$"))
async def admins(client: Client, query: CallbackQuery):
    if query.from_user.id != OWNER_ID:
        return await query.answer(
            "✗ This can only be used by the owner!", show_alert=True
        )

    cancel_user_listeners(client, query.from_user.id)
    admin_list = (
        ", ".join(f"`{a}`" for a in client.admins)
        if getattr(client, "admins", None)
        else "_None_"
    )
    msg = f"""<blockquote>**✦ Admin Settings:**</blockquote>
**Admin User IDs:** {admin_list}

__Use the appropriate button below to add or remove an admin based on your needs!__"""

    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ᴀᴅᴅ ᴀᴅᴍɪɴ", callback_data="add_admin"),
                InlineKeyboardButton("ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴ", callback_data="rm_admin"),
            ],
            [InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="settings")],
        ]
    )
    await safe_edit_or_send(client, query, msg, reply_markup)


@Client.on_callback_query(filters.regex("^add_admin$"))
async def add_new_admins(client: Client, query: CallbackQuery):
    if query.from_user.id != OWNER_ID:
        return await query.answer("✗ Only the owner can add admins!", show_alert=True)

    await safe_edit_or_send(
        client,
        query,
        "<blockquote>**➕ Add New Admins:**</blockquote>\nSend user IDs separated by a space in the next 60 seconds!\n\n**Example:** `838278682 83622928`",
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", callback_data="admins")]]
        ),
    )
    try:
        ids_msg = await wait_for_user_input(client, query.from_user.id, timeout=60)
    except ListenerTimeout:
        return await safe_edit_or_send(
            client,
            query,
            "**⏰ Timeout! Operation cancelled.**",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="admins")]]
            ),
        )
    except Exception:
        return

    ids = (ids_msg.text or "").split()
    added_count = 0
    try:
        for identifier in ids:
            uid = int(identifier)
            if uid not in client.admins:
                client.admins.append(uid)
                added_count += 1
                await client.mongodb.add_admin(uid)
    except Exception as e:
        return await ids_msg.reply(
            f"**Error:** `{e}`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="admins")]]
            ),
        )

    await admins(client, query)
    return await ids_msg.reply(
        f"__{added_count} admin {'ID' if added_count == 1 else 'IDs'} have been promoted!__"
    )


@Client.on_callback_query(filters.regex("^rm_admin$"))
async def remove_admins(client: Client, query: CallbackQuery):
    if query.from_user.id != OWNER_ID:
        return await query.answer(
            "✗ Only the owner can remove admins!", show_alert=True
        )

    await safe_edit_or_send(
        client,
        query,
        "<blockquote>**➖ Remove Admins:**</blockquote>\nSend user IDs separated by a space in the next 60 seconds!\n\n**Example:** `838278682 83622928`",
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", callback_data="admins")]]
        ),
    )
    try:
        ids_msg = await wait_for_user_input(client, query.from_user.id, timeout=60)
    except ListenerTimeout:
        return await safe_edit_or_send(
            client,
            query,
            "**⏰ Timeout! Operation cancelled.**",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="admins")]]
            ),
        )
    except Exception:
        return

    ids = (ids_msg.text or "").split()
    removed_count = 0
    try:
        for identifier in ids:
            uid = int(identifier)
            if uid == OWNER_ID:
                await client.send_message(
                    query.from_user.id,
                    "**⚠️ Cannot remove the owner from the admin list!**",
                )
                continue
            if uid in client.admins:
                client.admins.remove(uid)
                removed_count += 1
                await client.mongodb.remove_admin(uid)
    except Exception as e:
        return await ids_msg.reply(
            f"**Error:** `{e}`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="admins")]]
            ),
        )

    await admins(client, query)
    return await ids_msg.reply(
        f"__{removed_count} admin {'ID' if removed_count == 1 else 'IDs'} have been removed!__"
    )


@Client.on_message(filters.command(["stats", "usage"]))
async def usage_cmd(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply("✗ ᴛʜɪs ᴄᴀɴ ᴏɴʟʏ ʙᴇ ᴜsᴇᴅ ʙʏ ᴀᴅᴍɪɴs!")

    reply = await message.reply("<blockquote>›› ᴇxᴛʀᴀᴄᴛɪɴɢ ᴜsᴀɢᴇ ᴅᴀᴛᴀ...</blockquote>")

    try:
        total_users_list = await client.mongodb.full_userbase()
        total_users = len(total_users_list)
    except Exception:
        total_users = "ᴇʀʀᴏʀ"

    uptime_duration = datetime.now() - getattr(client, "uptime", datetime.now())
    days = uptime_duration.days
    hours, remainder = divmod(uptime_duration.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    uptime_str = f"{days}ᴅ {hours}ʜ {minutes}ᴍ"

    total, used, free = shutil.disk_usage("/")
    total_gb = total / (1024**3)
    used_gb = used / (1024**3)
    free_gb = free / (1024**3)
    disk_percent = (used / total) * 100

    ram = psutil.virtual_memory()
    total_ram = ram.total / (1024**3)
    used_ram = ram.used / (1024**3)
    free_ram = ram.available / (1024**3)
    ram_percent = ram.percent

    swap = psutil.swap_memory()
    total_swap = swap.total / (1024**3)
    used_swap = swap.used / (1024**3)
    free_swap = swap.free / (1024**3)
    swap_percent = swap.percent

    psutil.cpu_percent(interval=None)
    try:
        process = psutil.Process()
        process.cpu_percent(interval=None)
    except Exception:
        process = None

    await asyncio.sleep(0.5)
    cpu_usage = psutil.cpu_percent(interval=None)

    try:
        net_io = psutil.net_io_counters()
        bytes_sent = net_io.bytes_sent / (1024**2)
        bytes_recv = net_io.bytes_recv / (1024**2)
        network_status = "✓ ᴀᴠᴀɪʟᴀʙʟᴇ"
        net_section = f"""<blockquote>›› **ᴜᴘʟᴏᴀᴅᴇᴅ:** `{bytes_sent:.2f} ᴍʙ`
›› **ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ:** `{bytes_recv:.2f} ᴍʙ`</blockquote>"""
    except PermissionError:
        network_status = "✗ ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ"
        net_section = "<blockquote>›› **sᴛᴀᴛᴜs:** `ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ ᴏɴ ᴘʀᴏᴏᴛ`</blockquote>"

    if process:
        try:
            bot_cpu_usage = process.cpu_percent(interval=None)
            bot_memory_usage = process.memory_info().rss / (1024**2)
            bot_status = "✓ ʀᴜɴɴɪɴɢ"
        except Exception:
            bot_cpu_usage = 0.0
            bot_memory_usage = 0.0
            bot_status = "✗ ᴇʀʀᴏʀ"
    else:
        bot_cpu_usage = 0.0
        bot_memory_usage = 0.0
        bot_status = "✗ ᴇʀʀᴏʀ"

    disk_status = (
        "✓ ɴᴏʀᴍᴀʟ"
        if disk_percent < 80
        else "✗ ʜɪɢʜ" if disk_percent < 95 else "✗ ᴄʀɪᴛɪᴄᴀʟ"
    )
    ram_status = (
        "✓ ɴᴏʀᴍᴀʟ"
        if ram_percent < 80
        else "✗ ʜɪɢʜ" if ram_percent < 95 else "✗ ᴄʀɪᴛɪᴄᴀʟ"
    )
    cpu_status = (
        "✓ ɴᴏʀᴍᴀʟ" if cpu_usage < 80 else "✗ ʜɪɢʜ" if cpu_usage < 95 else "✗ ᴄʀɪᴛɪᴄᴀʟ"
    )

    msg = f"""<blockquote>✦ sʏsᴛᴇᴍ ᴜsᴀɢᴇ sᴛᴀᴛs</blockquote>

<blockquote><u>**≡ ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs:**</u></blockquote>
<blockquote>›› **ᴛᴏᴛᴀʟ ᴜsᴇʀs:** `{total_users}`
›› **ʙᴏᴛ sᴛᴀᴛᴜs:** {bot_status}
›› **ᴜᴘᴛɪᴍᴇ:** `{uptime_str}`
›› **ᴀᴅᴍɪɴs:** `{len(client.admins)}`
›› **ᴡᴏʀᴋᴇʀ ʙᴏᴛs:** `{len(getattr(client, 'worker_clients', {}))}`
›› **sᴇᴄᴏɴᴅᴀʀʏ ᴅʙs:** `{len(getattr(client, 'db_urls', []))}`</blockquote>

<blockquote><u>**≡ ᴅɪsᴋ ᴜsᴀɢᴇ:**</u></blockquote>
<blockquote>›› **ᴛᴏᴛᴀʟ:** `{total_gb:.2f} ɢʙ`
›› **ᴜsᴇᴅ:** `{used_gb:.2f} ɢʙ` ({disk_percent:.1f}%)
›› **ꜰʀᴇᴇ:** `{free_gb:.2f} ɢʙ`
›› **sᴛᴀᴛᴜs:** {disk_status}</blockquote>

<blockquote><u>**≡ ʀᴀᴍ ᴜsᴀɢᴇ:**</u></blockquote>
<blockquote>›› **ᴛᴏᴛᴀʟ:** `{total_ram:.2f} ɢʙ`
›› **ᴜsᴇᴅ:** `{used_ram:.2f} ɢʙ` ({ram_percent:.1f}%)
›› **ꜰʀᴇᴇ:** `{free_ram:.2f} ɢʙ`
›› **sᴛᴀᴛᴜs:** {ram_status}</blockquote>

<blockquote><u>**≡ sᴡᴀᴘ ᴜsᴀɢᴇ:**</u></blockquote>
<blockquote>›› **ᴛᴏᴛᴀʟ:** `{total_swap:.2f} ɢʙ`
›› **ᴜsᴇᴅ:** `{used_swap:.2f} ɢʙ` ({swap_percent:.1f}%)
›› **ꜰʀᴇᴇ:** `{free_swap:.2f} ɢʙ`</blockquote>

<blockquote><u>**≡ ᴄᴘᴜ & ɴᴇᴛᴡᴏʀᴋ:**</u></blockquote>
<blockquote>›› **ᴄᴘᴜ ᴜsᴀɢᴇ:** `{cpu_usage:.2f}%` {cpu_status}
›› **ɴᴇᴛᴡᴏʀᴋ:** {network_status}</blockquote>
{net_section}

<blockquote><u>**≡ ʙᴏᴛ ʀᴇsᴏᴜʀᴄᴇ ᴜsᴀɢᴇ:**</u></blockquote>
<blockquote>›› **ᴄᴘᴜ:** `{bot_cpu_usage:.2f}%`
›› **ᴍᴇᴍᴏʀʏ:** `{bot_memory_usage:.2f} ᴍʙ`</blockquote>"""

    await reply.edit_text(msg)


# =============================================================== #
#                USERS & BROADCAST HANDLERS                       #
# =============================================================== #


@Client.on_message(filters.command("users"))
async def user_count(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(
            getattr(client, "reply_text", "You are not authorized.")
        )
    total_users = await client.mongodb.full_userbase()
    await message.reply(f"**{len(total_users)} Users are using this bot currently!**")


async def _execute_broadcast(client: Client, message: Message, pin: bool = False):
    """Unified, high-efficiency broadcast engine for standard and pinned broadcasts."""
    if not is_admin(client, message.from_user.id):
        return await message.reply(
            getattr(client, "reply_text", "You are not authorized.")
        )

    if not message.reply_to_message:
        msg = await message.reply(
            "Use This Command As A Reply To Any Telegram Message Without Any Spaces."
        )
        await asyncio.sleep(8)
        return await msg.delete()

    query = await client.mongodb.full_userbase()
    broadcast_msg = message.reply_to_message
    total = 0
    successful = 0
    blocked = 0
    deleted = 0
    unsuccessful = 0

    pls_wait = await message.reply(
        "<blockquote><i>Broadcasting Message.. This will Take Some Time</i></blockquote>"
    )

    for chat_id in query:
        try:
            sent_msg = await broadcast_msg.copy(chat_id)
            successful += 1
            if pin:
                try:
                    await client.pin_chat_message(
                        chat_id=chat_id, message_id=sent_msg.id, both_sides=True
                    )
                except Exception:
                    pass
        except FloodWait as e:
            wait_time = getattr(e, "value", getattr(e, "x", 5))
            await asyncio.sleep(wait_time)
            try:
                sent_msg = await broadcast_msg.copy(chat_id)
                successful += 1
                if pin:
                    await client.pin_chat_message(
                        chat_id=chat_id, message_id=sent_msg.id, both_sides=True
                    )
            except Exception:
                unsuccessful += 1
        except UserIsBlocked:
            await client.mongodb.del_user(chat_id)
            blocked += 1
        except (InputUserDeactivated, PeerIdInvalid):
            await client.mongodb.del_user(chat_id)
            deleted += 1
        except Exception as e:
            client.LOGGER(__name__, client.name).warning(
                f"Failed to broadcast to {chat_id}: {e}"
            )
            unsuccessful += 1

        total += 1
        if total % 50 == 0:
            try:
                await pls_wait.edit(
                    f"<blockquote><b><u>Broadcasting In Progress...</u></b></blockquote>\n"
                    f"<b>Processed:</b> <code>{total}/{len(query)}</code>\n"
                    f"<b>Successful:</b> <code>{successful}</code> | <b>Blocked:</b> <code>{blocked}</code>"
                )
            except Exception:
                pass

    status = f"""<blockquote><b><u>Broadcast Completed</u></b></blockquote>
<blockquote expandable><b>Total Users :</b> <code>{total}</code>
<b>Successful :</b> <code>{successful}</code>
<b>Blocked Users :</b> <code>{blocked}</code>
<b>Deleted Accounts :</b> <code>{deleted}</code>
<b>Unsuccessful :</b> <code>{unsuccessful}</code></blockquote>"""

    return await pls_wait.edit(status)


@Client.on_message(filters.private & filters.command("broadcast"))
async def send_text(client: Client, message: Message):
    await _execute_broadcast(client, message, pin=False)


@Client.on_message(filters.private & filters.command("pbroadcast"))
async def pin_bdcst_text(client: Client, message: Message):
    await _execute_broadcast(client, message, pin=True)


# =============================================================== #
#                MULTI-DATABASE (/datadb) MANAGER                 #
# =============================================================== #


def ensure_multidb_attrs(client):
    """Ensure client has multi-database attributes initialized (Unlimited DBs)."""
    if not hasattr(client, "multi_db_enabled"):
        client.multi_db_enabled = True
    if not hasattr(client, "db_urls"):
        client.db_urls = []


def get_datadb_main_menu(client):
    """Generate text and buttons for the main /datadb menu."""
    ensure_multidb_attrs(client)

    on_text = "🟢 On ✓" if client.multi_db_enabled else "On"
    off_text = "🔴 Off ✓" if not client.multi_db_enabled else "Off"
    status_str = "ENABLED ✅" if client.multi_db_enabled else "DISABLED ❌"

    msg = f"""<blockquote>**✦ ᴍᴜʟᴛɪ ᴅᴀᴛᴀʙᴀsᴇ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ ✦**</blockquote>

›› **ᴍᴜʟᴛɪ ᴅᴀᴛᴀʙᴀsᴇ sᴛᴀᴛᴜs:** `{status_str}`
›› **ᴛᴏᴛᴀʟ ᴄᴏɴғɪɢᴜʀᴇᴅ ᴜʀʟs:** `{len(client.db_urls)}` (Unlimited Supported)

__Use the buttons below to manage your Multi-Database URLs and settings!__"""

    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(on_text, callback_data="multidb_on"),
                InlineKeyboardButton(off_text, callback_data="multidb_off"),
            ],
            [
                InlineKeyboardButton("➕ Add url", callback_data="multidb_add"),
                InlineKeyboardButton("📋 url list", callback_data="multidb_list"),
            ],
            [
                InlineKeyboardButton(
                    "➖ Remove url", callback_data="multidb_rm_prompt"
                ),
                InlineKeyboardButton(
                    "🗑️ Remove All url", callback_data="multidb_rm_all"
                ),
            ],
            [InlineKeyboardButton("◂ ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="settings")],
        ]
    )
    return msg, reply_markup


@Client.on_message(filters.command("datadb") & filters.private)
async def datadb_command(client: Client, message: Message):
    """Handle /datadb command to display the Multi Database interactive panel."""
    if not is_admin(client, message.from_user.id):
        return await message.reply(
            getattr(
                client,
                "reply_text",
                "**⚠️ You are not authorized to use this command!**",
            )
        )

    cancel_user_listeners(client, message.from_user.id)
    msg, reply_markup = get_datadb_main_menu(client)
    await message.reply_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^datadb_menu$"))
async def datadb_menu_cb(client: Client, query: CallbackQuery):
    """Return to or refresh the main /datadb menu."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    cancel_user_listeners(client, query.from_user.id)
    msg, reply_markup = get_datadb_main_menu(client)
    await safe_edit_or_send(client, query, msg, reply_markup)


@Client.on_callback_query(filters.regex("^multidb_(on|off)$"))
async def multidb_toggle_cb(client: Client, query: CallbackQuery):
    """Switch Multi Database On or Off."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    ensure_multidb_attrs(client)
    action = query.data.split("_")[1]
    client.multi_db_enabled = action == "on"

    await client.mongodb.set_multi_db_status(client.multi_db_enabled)

    await query.answer(
        f"Multi Database turned {'ON ✅' if client.multi_db_enabled else 'OFF ❌'}"
    )
    await datadb_menu_cb(client, query)


@Client.on_callback_query(filters.regex("^multidb_add$"))
async def multidb_add_cb(client: Client, query: CallbackQuery):
    """Add an unlimited number of Database URLs."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    ensure_multidb_attrs(client)
    await safe_edit_or_send(
        client,
        query,
        "<blockquote>**➕ ᴀᴅᴅ ᴅᴀᴛᴀʙᴀsᴇ ᴜʀʟ**</blockquote>\n\n"
        "Send your **Database Name** and **MongoDB URL** separated by a space in the next 60 seconds!\n\n"
        "**Example:**\n`DB1 mongodb+srv://user:pass@cluster.mongodb.net/`\n\n"
        "*(Or simply send the `mongodb+srv://...` URL directly and a name will be auto-assigned)*",
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", callback_data="datadb_menu")]]
        ),
    )

    try:
        ask_info = await wait_for_user_input(client, query.from_user.id, timeout=60)
    except ListenerTimeout:
        return await safe_edit_or_send(
            client,
            query,
            "**⏰ Timeout! Operation cancelled.**",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="datadb_menu")]]
            ),
        )
    except Exception:
        return

    text = (ask_info.text or "").strip()
    if not text:
        return await ask_info.reply(
            "**✗ Invalid input!**",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="datadb_menu")]]
            ),
        )
    parts = text.split(maxsplit=1)

    if len(parts) == 1:
        url = parts[0]
        name = f"Database_{len(client.db_urls) + 1}"
    else:
        name, url = parts[0], parts[1].strip()

    for entry in client.db_urls:
        if entry["url"] == url:
            return await ask_info.reply(
                "**✗ This Database URL is already added in the list!**",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="datadb_menu")]]
                ),
            )

    short_id = "".join(
        secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6)
    )
    new_db_entry = {
        "id": short_id,
        "name": name,
        "url": url,
        "added_by": query.from_user.id,
    }
    client.db_urls.append(new_db_entry)
    await client.mongodb.add_db_url(new_db_entry)

    await datadb_menu_cb(client, query)
    await ask_info.reply(
        f"**✓ ᴅᴀᴛᴀʙᴀsᴇ ᴜʀʟ ᴀᴅᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!**\n\n"
        f"›› **ɴᴀᴍᴇ:** `{name}`\n"
        f"›› **ᴛᴏᴛᴀʟ ᴅʙs:** `{len(client.db_urls)}`"
    )


@Client.on_callback_query(filters.regex("^multidb_list$"))
async def multidb_list_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    ensure_multidb_attrs(client)
    if not client.db_urls:
        return await query.answer("No Database URLs configured yet!", show_alert=True)

    buttons = []
    for idx, entry in enumerate(client.db_urls, 1):
        db_id = entry["id"]
        db_name = entry["name"]
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{idx}. {db_name}", callback_data=f"multidb_info_{db_id}"
                ),
                InlineKeyboardButton(
                    "remove url", callback_data=f"multidb_del_{db_id}"
                ),
            ]
        )

    buttons.append(
        [InlineKeyboardButton("◂ ʙᴀᴄᴋ ᴛᴏ ᴅᴀᴛᴀᴅʙ", callback_data="datadb_menu")]
    )

    msg = f"""<blockquote>**📋 ᴄᴏɴғɪɢᴜʀᴇᴅ ᴅᴀᴛᴀʙᴀsᴇ ᴜʀʟ ʟɪsᴛ ({len(client.db_urls)})**</blockquote>

Click on a **Url name** to view its details, or click **remove url** next to it to delete it immediately:"""

    await safe_edit_or_send(client, query, msg, InlineKeyboardMarkup(buttons))


@Client.on_callback_query(filters.regex("^multidb_info_(.+)$"))
async def multidb_info_cb(client: Client, query: CallbackQuery):
    ensure_multidb_attrs(client)
    db_id = query.data.split("multidb_info_")[1]

    for entry in client.db_urls:
        if entry["id"] == db_id:
            url_str = entry["url"]
            masked_url = (
                url_str[:22] + "..." + url_str[-12:] if len(url_str) > 38 else url_str
            )
            return await query.answer(
                f"🗂 Name: {entry['name']}\n🔗 URL: {masked_url}", show_alert=True
            )
    await query.answer("Database entry not found!", show_alert=True)


@Client.on_callback_query(filters.regex("^multidb_del_(.+)$"))
async def multidb_del_single_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    ensure_multidb_attrs(client)
    db_id = query.data.split("multidb_del_")[1]

    removed_entry = None
    for entry in list(client.db_urls):
        if entry["id"] == db_id:
            removed_entry = entry
            client.db_urls.remove(entry)
            break

    if not removed_entry:
        return await query.answer("URL already removed!", show_alert=True)

    await client.mongodb.remove_db_url(db_id)
    await query.answer(f"Removed: {removed_entry['name']} ✅")

    if client.db_urls:
        await multidb_list_cb(client, query)
    else:
        await datadb_menu_cb(client, query)


@Client.on_callback_query(filters.regex("^multidb_rm_prompt$"))
async def multidb_rm_prompt_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    ensure_multidb_attrs(client)
    if not client.db_urls:
        return await query.answer("No Database URLs to remove!", show_alert=True)

    await safe_edit_or_send(
        client,
        query,
        "<blockquote>**➖ ʀᴇᴍᴏᴠᴇ ᴅᴀᴛᴀʙᴀsᴇ ᴜʀʟ**</blockquote>\n\n"
        "Send the **Url Name** or full **MongoDB URL** you want to remove in the next 60 seconds:\n\n"
        "__Tip: You can also remove URLs with 1 click from the `url list` button!__",
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", callback_data="datadb_menu")]]
        ),
    )
    try:
        ask_rm = await wait_for_user_input(client, query.from_user.id, timeout=60)
    except ListenerTimeout:
        return await safe_edit_or_send(
            client,
            query,
            "**⏰ Timeout! Operation cancelled.**",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="datadb_menu")]]
            ),
        )
    except Exception:
        return

    target = (ask_rm.text or "").strip()
    removed_entry = None
    for entry in list(client.db_urls):
        if (
            entry["name"].lower() == target.lower()
            or entry["url"] == target
            or entry["id"] == target
        ):
            removed_entry = entry
            client.db_urls.remove(entry)
            break

    if not removed_entry:
        return await ask_rm.reply(
            f"**✗ Could not find any Database URL matching `{target}`!**",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="datadb_menu")]]
            ),
        )

    await client.mongodb.remove_db_url(removed_entry["id"])
    await datadb_menu_cb(client, query)
    await ask_rm.reply(
        f"**✓ Successfully removed Database URL:** `{removed_entry['name']}`"
    )


@Client.on_callback_query(filters.regex("^multidb_rm_all$"))
async def multidb_rm_all_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    ensure_multidb_attrs(client)
    if not client.db_urls:
        return await query.answer(
            "Database URL list is already empty!", show_alert=True
        )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Yes, Remove All", callback_data="multidb_confirm_rm_all"
                ),
                InlineKeyboardButton("❌ Cancel", callback_data="datadb_menu"),
            ]
        ]
    )
    await safe_edit_or_send(
        client,
        query,
        f"**⚠️ Are you sure you want to remove ALL ({len(client.db_urls)}) Database URLs?**\n\n"
        "__This action will clear all configured database URLs!__",
        markup,
    )


@Client.on_callback_query(filters.regex("^multidb_confirm_rm_all$"))
async def multidb_confirm_rm_all_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    ensure_multidb_attrs(client)
    client.db_urls.clear()
    await client.mongodb.clear_all_db_urls()

    await query.answer("✅ All Database URLs have been removed!", show_alert=True)
    await datadb_menu_cb(client, query)


# =============================================================== #
#                DB CHANNELS & GENERAL COMMANDS                   #
# =============================================================== #


def _build_db_channels_dashboard(client):
    db_channels = getattr(client, "db_channels", {})
    primary_db = getattr(client, "primary_db_channel", client.db)

    if db_channels:
        channel_list = []
        for channel_id_str, channel_data in db_channels.items():
            channel_name = channel_data.get("name", "ᴜɴᴋɴᴏᴡɴ")
            is_primary = (
                "✓ ᴘʀɪᴍᴀʀʏ" if channel_data.get("is_primary", False) else "• sᴇᴄᴏɴᴅᴀʀʏ"
            )
            is_active = (
                "✓ ᴀᴄᴛɪᴠᴇ" if channel_data.get("is_active", True) else "✗ ɪɴᴀᴄᴛɪᴠᴇ"
            )
            channel_list.append(
                f"• `{channel_name}` (`{channel_id_str}`)\n  {is_primary} | {is_active}"
            )
        channels_display = "\n\n".join(channel_list)
    else:
        channels_display = "_ɴᴏ ᴀᴅᴅɪᴛɪᴏɴᴀʟ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs ᴄᴏɴғɪɢᴜʀᴇᴅ_"

    msg = f"""<blockquote>✦ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs ᴍᴀɴᴀɢᴇᴍᴇɴᴛ</blockquote>

›› **ᴄᴜʀʀᴇɴᴛ ᴘʀɪᴍᴀʀʏ ᴅʙ:** `{primary_db}`
›› **ᴛᴏᴛᴀʟ ᴅʙ ᴄʜᴀɴɴᴇʟs:** `{len(db_channels)}`

**ᴄᴏɴғɪɢᴜʀᴇᴅ ᴄʜᴀɴɴᴇʟs:**
{channels_display}

__ᴜsᴇ ᴛʜᴇ ʙᴜᴛᴛᴏɴs ʙᴇʟᴏᴡ ᴛᴏ ᴍᴀɴᴀɢᴇ ʏᴏᴜʀ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs!__"""

    reply_markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("›› ᴀᴅᴅ ᴅʙ ᴄʜᴀɴɴᴇʟ", callback_data="add_db_channel")],
            [
                InlineKeyboardButton(
                    "›› ʀᴇᴍᴏᴠᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ", callback_data="rm_db_channel"
                )
            ],
            [InlineKeyboardButton("›› sᴇᴛ ᴘʀɪᴍᴀʀʏ", callback_data="set_primary_db")],
            [
                InlineKeyboardButton(
                    "›› ᴛᴏɢɢʟᴇ sᴛᴀᴛᴜs", callback_data="toggle_db_status"
                )
            ],
            [InlineKeyboardButton("›› ᴠɪᴇᴡ ᴅᴇᴛᴀɪʟs", callback_data="db_details")],
            [InlineKeyboardButton("‹ ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="settings")],
        ]
    )
    return msg, reply_markup


@Client.on_message(filters.command(["db", "dbchannels"]) & filters.private)
async def db_channels_command(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(client.reply_text)

    cancel_user_listeners(client, message.from_user.id)
    msg, reply_markup = _build_db_channels_dashboard(client)
    await message.reply(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^db_details$"))
async def db_details(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    db_channels = getattr(client, "db_channels", {})
    primary_db = getattr(client, "primary_db_channel", client.db)

    msg = f"""<blockquote>✦ ᴅᴇᴛᴀɪʟᴇᴅ ᴅʙ ᴄʜᴀɴɴᴇʟs ɪɴғᴏʀᴍᴀᴛɪᴏɴ</blockquote>

›› **ᴘʀɪᴍᴀʀʏ ᴅʙ ᴄʜᴀɴɴᴇʟ:** `{primary_db}`
›› **ᴛᴏᴛᴀʟ ᴄᴏɴғɪɢᴜʀᴇᴅ:** `{len(db_channels)}`

"""
    if db_channels:
        for i, (channel_id_str, channel_data) in enumerate(db_channels.items(), 1):
            channel_name = channel_data.get("name", "ᴜɴᴋɴᴏᴡɴ")
            is_primary = channel_data.get("is_primary", False)
            is_active = channel_data.get("is_active", True)
            added_by = channel_data.get("added_by", "ᴜɴᴋɴᴏᴡɴ")

            status_emoji = "✓" if is_primary else "•"
            active_emoji = "✓" if is_active else "✗"

            msg += f"""**{i}. {channel_name}**
• **ɪᴅ:** `{channel_id_str}`
• **sᴛᴀᴛᴜs:** {status_emoji} {'ᴘʀɪᴍᴀʀʏ' if is_primary else 'sᴇᴄᴏɴᴅᴀʀʏ'}
• **ᴀᴄᴛɪᴠᴇ:** {active_emoji} {'ʏᴇs' if is_active else 'ɴᴏ'}
• **ᴀᴅᴅᴇᴅ ʙʏ:** `{added_by}`

"""
    else:
        msg += "_ɴᴏ ᴀᴅᴅɪᴛɪᴏɴᴀʟ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs ᴄᴏɴғɪɢᴜʀᴇᴅ_\n\n"

    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "‹ ʙᴀᴄᴋ ᴛᴏ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ", callback_data="back_to_db_management"
                )
            ]
        ]
    )
    await safe_edit_or_send(client, query, msg, reply_markup)


@Client.on_callback_query(filters.regex("^back_to_db_management$"))
async def back_to_db_management(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    msg, reply_markup = _build_db_channels_dashboard(client)
    await safe_edit_or_send(client, query, msg, reply_markup)


@Client.on_message(filters.command(["adddb", "add_db"]) & filters.private)
async def quick_add_db(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(client.reply_text)

    args = message.text.split()
    if len(args) < 2:
        return await message.reply("""<blockquote>✦ ᴀᴅᴅ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ</blockquote>

›› **ᴜsᴀɢᴇ:** `/adddb <channel_id>`
›› **ᴇxᴀᴍᴘʟᴇ:** `/adddb -1001234567890`

**ɴᴏᴛᴇ:** ᴍᴀᴋᴇ sᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪs ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ!""")

    try:
        channel_id = int(args[1])
    except ValueError:
        return await message.reply(
            "**✗ ɪɴᴠᴀʟɪᴅ ᴄʜᴀɴɴᴇʟ ɪᴅ! ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴠᴀʟɪᴅ ɴᴇɢᴀᴛɪᴠᴇ ɪɴᴛᴇɢᴇʀ.**"
        )

    db_channels = getattr(client, "db_channels", {})
    if str(channel_id) in db_channels:
        return await message.reply(
            f"**✗ ᴄʜᴀɴɴᴇʟ `{channel_id}` ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴅᴅᴇᴅ ᴀs ᴀ ᴅʙ ᴄʜᴀɴɴᴇʟ!**"
        )

    try:
        chat = await client.get_chat(channel_id)
        test_msg = await client.send_message(
            chat_id=channel_id, text="ᴛᴇsᴛɪɴɢ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴀᴄᴄᴇss"
        )
        await test_msg.delete()

        channel_data = {
            "name": chat.title,
            "is_primary": len(db_channels) == 0,
            "is_active": True,
            "added_by": message.from_user.id,
        }

        await client.mongodb.add_db_channel(channel_id, channel_data)
        if not hasattr(client, "db_channels"):
            client.db_channels = {}
        client.db_channels[str(channel_id)] = channel_data

        if channel_data["is_primary"]:
            client.primary_db_channel = channel_id
            await client.mongodb.set_primary_db_channel(channel_id)

        await message.reply(f"""**✓ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!**

›› **ᴄʜᴀɴɴᴇʟ:** `{chat.title}`
›› **ɪᴅ:** `{channel_id}`
›› **sᴛᴀᴛᴜs:** {'ᴘʀɪᴍᴀʀʏ' if channel_data['is_primary'] else 'sᴇᴄᴏɴᴅᴀʀʏ'}""")
    except Exception as e:
        await message.reply(f"**✗ ᴇʀʀᴏʀ ᴀᴄᴄᴇssɪɴɢ ᴄʜᴀɴɴᴇʟ:** `{str(e)}`")


@Client.on_message(filters.command(["removedb", "rm_db"]) & filters.private)
async def quick_remove_db(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(client.reply_text)

    args = message.text.split()
    if len(args) < 2:
        db_channels = getattr(client, "db_channels", {})
        if not db_channels:
            return await message.reply("**✗ ɴᴏ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs ᴛᴏ ʀᴇᴍᴏᴠᴇ!**")

        msg = "<blockquote>✦ ʀᴇᴍᴏᴠᴇ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ</blockquote>\n›› **ᴜsᴀɢᴇ:** `/removedb <channel_id>`\n\n**ᴀᴠᴀɪʟᴀʙʟᴇ ᴄʜᴀɴɴᴇʟs:**\n"
        for channel_id_str, channel_data in db_channels.items():
            channel_name = channel_data.get("name", "ᴜɴᴋɴᴏᴡɴ")
            is_primary = " (ᴘʀɪᴍᴀʀʏ)" if channel_data.get("is_primary", False) else ""
            msg += f"• `{channel_name}` - `{channel_id_str}`{is_primary}\n"
        return await message.reply(msg)

    try:
        channel_id = int(args[1])
    except ValueError:
        return await message.reply("**✗ ɪɴᴠᴀʟɪᴅ ᴄʜᴀɴɴᴇʟ ɪᴅ!**")

    db_channels = getattr(client, "db_channels", {})
    if str(channel_id) not in db_channels:
        return await message.reply(
            f"**✗ ᴄʜᴀɴɴᴇʟ `{channel_id}` ɪs ɴᴏᴛ ɪɴ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟs ʟɪsᴛ!**"
        )

    if db_channels[str(channel_id)].get("is_primary", False) and len(db_channels) > 1:
        return await message.reply(
            "**✗ ᴄᴀɴɴᴏᴛ ʀᴇᴍᴏᴠᴇ ᴘʀɪᴍᴀʀʏ ᴄʜᴀɴɴᴇʟ! sᴇᴛ ᴀɴᴏᴛʜᴇʀ ᴄʜᴀɴɴᴇʟ ᴀs ᴘʀɪᴍᴀʀʏ ғɪʀsᴛ.**"
        )

    channel_name = db_channels[str(channel_id)].get("name", "ᴜɴᴋɴᴏᴡɴ")
    await client.mongodb.remove_db_channel(channel_id)
    del client.db_channels[str(channel_id)]

    await message.reply(
        f"**✓ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ:** `{channel_name}` (`{channel_id}`)"
    )


@Client.on_callback_query(filters.regex("^home$"))
async def home(client: Client, query: CallbackQuery):
    cancel_user_listeners(client, query.from_user.id)
    buttons = [
        [
            InlineKeyboardButton("Help", callback_data="about"),
            InlineKeyboardButton("Close", callback_data="close"),
        ]
    ]
    if is_admin(client, query.from_user.id):
        buttons.insert(
            0, [InlineKeyboardButton("⛩️ ꜱᴇᴛᴛɪɴɢꜱ ⛩️", callback_data="settings")]
        )
    text = client.messages.get("START", "No Start Message").format(
        first=query.from_user.first_name or "",
        last=query.from_user.last_name or "",
        username=(
            None if not query.from_user.username else "@" + query.from_user.username
        ),
        mention=query.from_user.mention,
        id=query.from_user.id,
    )
    await safe_edit_or_send(client, query, text, InlineKeyboardMarkup(buttons))


@Client.on_callback_query(filters.regex("^about$"))
async def about(client: Client, query: CallbackQuery):
    cancel_user_listeners(client, query.from_user.id)
    buttons = [
        [
            InlineKeyboardButton("Back", callback_data="home"),
            InlineKeyboardButton("Close", callback_data="close"),
        ]
    ]
    text = client.messages.get("ABOUT", "No About Message").format(
        owner_id=client.owner,
        bot_username=client.username,
        first=query.from_user.first_name or "",
        last=query.from_user.last_name or "",
        username=(
            None if not query.from_user.username else "@" + query.from_user.username
        ),
        mention=query.from_user.mention,
        id=query.from_user.id,
    )
    await safe_edit_or_send(client, query, text, InlineKeyboardMarkup(buttons))


@Client.on_callback_query(filters.regex("^close$"))
async def close(client: Client, query: CallbackQuery):
    cancel_user_listeners(client, query.from_user.id)
    try:
        await query.message.delete()
    except Exception:
        pass
    try:
        if query.message.reply_to_message:
            await query.message.reply_to_message.delete()
    except Exception:
        pass


@Client.on_message(filters.command("ban"))
async def ban(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(client.reply_text)
    try:
        user_ids = message.text.split(maxsplit=1)[1]
        c = 0
        for user_id in user_ids.split():
            user_id = int(user_id)
            if is_admin(client, user_id):
                continue
            if not await client.mongodb.present_user(user_id):
                await client.mongodb.add_user(user_id, True)
            else:
                await client.mongodb.ban_user(user_id)
            c += 1
        return await message.reply(f"__{c} users have been banned!__")
    except Exception as e:
        return await message.reply(f"**Error:** `{e}`")


@Client.on_message(filters.command("unban"))
async def unban(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(client.reply_text)
    try:
        user_ids = message.text.split(maxsplit=1)[1]
        c = 0
        for user_id in user_ids.split():
            user_id = int(user_id)
            if is_admin(client, user_id):
                continue
            if not await client.mongodb.present_user(user_id):
                await client.mongodb.add_user(user_id)
            else:
                await client.mongodb.unban_user(user_id)
            c += 1
        return await message.reply(f"__{c} users have been unbanned!__")
    except Exception as e:
        return await message.reply(f"**Error:** `{e}`")


# =============================================================== #
#                LINK GENERATOR HANDLERS                          #
# =============================================================== #


async def get_db_channels_info(client):
    db_channels = getattr(client, "db_channels", {})
    primary_db = getattr(client, "primary_db_channel", client.db)

    if not db_channels:
        try:
            primary_chat = await client.get_chat(primary_db)
            if hasattr(primary_chat, "invite_link") and primary_chat.invite_link:
                return f"<blockquote>✦ ᴘʀɪᴍᴀʀʏ ᴅʙ ᴄʜᴀɴɴᴇʟ: <a href='{primary_chat.invite_link}'>{primary_chat.title}</a></blockquote>"
            else:
                return f"<blockquote>✦ ᴘʀɪᴍᴀʀʏ ᴅʙ ᴄʜᴀɴɴᴇʟ: {primary_chat.title} (`{primary_db}`)</blockquote>"
        except Exception:
            return f"<blockquote>✦ ᴘʀɪᴍᴀʀʏ ᴅʙ ᴄʜᴀɴɴᴇʟ: `{primary_db}`</blockquote>"

    channels_info = ["<blockquote>✦ ᴀᴠᴀɪʟᴀʙʟᴇ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs:</blockquote>"]
    for channel_id_str, channel_data in db_channels.items():
        channel_name = channel_data.get("name", "ᴜɴᴋɴᴏᴡɴ")
        is_primary_text = (
            "✦ ᴘʀɪᴍᴀʀʏ" if channel_data.get("is_primary", False) else "• sᴇᴄᴏɴᴅᴀʀʏ"
        )

        try:
            chat = await client.get_chat(int(channel_id_str))
            if hasattr(chat, "invite_link") and chat.invite_link:
                channels_info.append(
                    f"{is_primary_text}: <a href='{chat.invite_link}'>{channel_name}</a>"
                )
            else:
                channels_info.append(
                    f"{is_primary_text}: {channel_name} (`{channel_id_str}`)"
                )
        except Exception:
            channels_info.append(
                f"{is_primary_text}: {channel_name} (`{channel_id_str}`)"
            )

    return "\n".join(channels_info)


@Client.on_message(filters.private & filters.command("batch"))
async def batch(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(client.reply_text)

    cancel_user_listeners(client, message.from_user.id)
    db_channels_info = await get_db_channels_info(client)

    while True:
        try:
            await message.reply(
                f"""<blockquote>ꜰᴏʀᴡᴀʀᴅ ᴛʜᴇ ꜰɪʀsᴛ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴅʙ ᴄʜᴀɴɴᴇʟ (ᴡɪᴛʜ ǫᴜᴏᴛᴇs)..</blockquote>
{db_channels_info}

<blockquote>ᴏʀ sᴇɴᴅ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ ʟɪɴᴋ</blockquote>"""
            )
            first_message = await wait_for_user_input(
                client, message.from_user.id, timeout=60
            )
        except Exception:
            return
        f_msg_id, source_channel_id = await get_message_id(client, first_message)
        if f_msg_id:
            break
        else:
            await first_message.reply(
                "<blockquote>✗ ᴇʀʀᴏʀ</blockquote>\n\nᴛʜɪs ꜰᴏʀᴡᴀʀᴅᴇᴅ ᴘᴏsᴛ ɪs ɴᴏᴛ ꜰʀᴏᴍ ᴍʏ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴏʀ ᴛʜɪs ʟɪɴᴋ ɪs ɴᴏᴛ ᴛᴀᴋᴇɴ ꜰʀᴏᴍ ᴅʙ ᴄʜᴀɴɴᴇʟ",
                quote=True,
            )
            continue

    while True:
        try:
            await message.reply(
                f"""<blockquote>ꜰᴏʀᴡᴀʀᴅ ᴛʜᴇ ʟᴀsᴛ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴅʙ ᴄʜᴀɴɴᴇʟ (ᴡɪᴛʜ ǫᴜᴏᴛᴇs)..</blockquote>
{db_channels_info}

<blockquote>ᴏʀ sᴇɴᴅ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ ʟɪɴᴋ</blockquote>"""
            )
            second_message = await wait_for_user_input(
                client, message.from_user.id, timeout=60
            )
        except Exception:
            return
        s_msg_id, _ = await get_message_id(client, second_message)
        if s_msg_id:
            break
        else:
            await second_message.reply(
                "<blockquote>✗ ᴇʀʀᴏʀ</blockquote>\n\nᴛʜɪs ꜰᴏʀᴡᴀʀᴅᴇᴅ ᴘᴏsᴛ ɪs ɴᴏᴛ ꜰʀᴏᴍ ᴍʏ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴏʀ ᴛʜɪs ʟɪɴᴋ ɪs ɴᴏᴛ ᴛᴀᴋᴇɴ ꜰʀᴏᴍ ᴅʙ ᴄʜᴀɴɴᴇʟ",
                quote=True,
            )
            continue

    string_val = (
        f"get-{f_msg_id * abs(source_channel_id)}-{s_msg_id * abs(source_channel_id)}"
    )
    base64_string = await encode(string_val)
    link = f"https://t.me/{client.username}?start={base64_string}"
    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔁 sʜᴀʀᴇ ᴜʀʟ", url=f"https://telegram.me/share/url?url={link}"
                )
            ]
        ]
    )
    await second_message.reply_text(
        f"<blockquote>✓ ʜᴇʀᴇ ɪs ʏᴏᴜʀ ʙᴀᴛᴄʜ ʟɪɴᴋ</blockquote>\n\n<code>{link}</code>",
        quote=True,
        reply_markup=reply_markup,
    )


@Client.on_message(filters.private & filters.command("genlink"))
async def link_generator(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(client.reply_text)

    cancel_user_listeners(client, message.from_user.id)
    db_channels_info = await get_db_channels_info(client)

    while True:
        try:
            await message.reply(
                f"""<blockquote>ꜰᴏʀᴡᴀʀᴅ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ (ᴡɪᴛʜ ǫᴜᴏᴛᴇs)..</blockquote>

{db_channels_info}

<blockquote>ᴏʀ sᴇɴᴅ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴘᴏsᴛ ʟɪɴᴋ</blockquote>"""
            )
            channel_message = await wait_for_user_input(
                client, message.from_user.id, timeout=60
            )
        except Exception:
            return
        msg_id, source_channel_id = await get_message_id(client, channel_message)
        if msg_id:
            break
        else:
            await channel_message.reply(
                "<blockquote>✗ ᴇʀʀᴏʀ</blockquote>\n\nᴛʜɪs ꜰᴏʀᴡᴀʀᴅᴇᴅ ᴘᴏsᴛ ɪs ɴᴏᴛ ꜰʀᴏᴍ ᴍʏ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴏʀ ᴛʜɪs ʟɪɴᴋ ɪs ɴᴏᴛ ᴛᴀᴋᴇɴ ꜰʀᴏᴍ ᴅʙ ᴄʜᴀɴɴᴇʟ",
                quote=True,
            )
            continue

    base64_string = await encode(f"get-{msg_id * abs(source_channel_id)}")
    link = f"https://t.me/{client.username}?start={base64_string}"
    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔁 sʜᴀʀᴇ ᴜʀʟ", url=f"https://telegram.me/share/url?url={link}"
                )
            ]
        ]
    )
    await channel_message.reply_text(
        f"<blockquote>✓ ʜᴇʀᴇ ɪs ʏᴏᴜʀ ʟɪɴᴋ</blockquote>\n\n<code>{link}</code>",
        quote=True,
        reply_markup=reply_markup,
    )


@Client.on_message(filters.private & filters.command("nbatch"))
async def nbatch(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(client.reply_text)

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply(
            "<blockquote>✗ ɪɴᴠᴀʟɪᴅ ꜰᴏʀᴍᴀᴛ!</blockquote> ᴜsᴇ: `/nbatch {number}`"
        )
        return

    cancel_user_listeners(client, message.from_user.id)
    batch_size = int(args[1])
    db_channels_info = await get_db_channels_info(client)

    while True:
        try:
            await message.reply(
                f"""<blockquote>🚀 sᴇɴᴅ ᴅʙ ᴄʜᴀɴɴᴇʟ ꜰɪʀsᴛ ᴍᴇssᴀɢᴇ ʟɪɴᴋ (ᴡɪᴛʜ ǫᴜᴏᴛᴇs)...</blockquote>

{db_channels_info}"""
            )
            first_message = await wait_for_user_input(
                client, message.from_user.id, timeout=60
            )
        except Exception:
            return

        f_msg_id, source_channel_id = await get_message_id(client, first_message)
        if f_msg_id:
            break
        else:
            await first_message.reply(
                "<blockquote>😫 ɪɴᴠᴀʟɪᴅ!</blockquote> sᴇɴᴅ ᴄᴏʀʀᴇᴄᴛ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴍᴇssᴀɢᴇ ʟɪɴᴋ.",
                quote=True,
            )
            continue

    s_msg_id = f_msg_id + batch_size - 1
    string_val = (
        f"get-{f_msg_id * abs(source_channel_id)}-{s_msg_id * abs(source_channel_id)}"
    )
    base64_string = await encode(string_val)
    link = f"https://t.me/{client.username}?start={base64_string}"

    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📫 ʏᴏᴜʀ ʙᴀᴛᴄʜ ᴜʀʟ", url=f"https://telegram.me/share/url?url={link}"
                )
            ]
        ]
    )

    await first_message.reply_text(
        f"<blockquote>✓ ʜᴇʀᴇ ɪs ʏᴏᴜʀ ʙᴀᴛᴄʜ ʟɪɴᴋ</blockquote>\n\n<code>{link}</code>",
        quote=True,
        reply_markup=reply_markup,
    )


# =============================================================== #
#                CHANNEL POST HANDLERS                            #
# =============================================================== #


def generate_random_id(length: int = 8) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def is_not_numeric_or_listening(client: Client, _, message: Message):
    """
    Prevents the catch-all file uploader from intercepting messages
    when an admin is answering a Settings / DataDB / Bots / FSub prompt!
    """
    user_id = message.from_user.id if message.from_user else None
    chat_id = message.chat.id if message.chat else None
    if user_id and has_active_listener(client, user_id, chat_id):
        return False
    if message.text and message.text.strip().lstrip("+-").isdigit():
        return False
    return True


not_listening_filter = filters.create(is_not_numeric_or_listening)


@Client.on_message(
    filters.private
    & ~filters.command(
        [
            "start",
            "users",
            "broadcast",
            "batch",
            "genlink",
            "nbatch",
            "usage",
            "stats",
            "pbroadcast",
            "ban",
            "unban",
            "db",
            "dbchannels",
            "adddb",
            "add_db",
            "removedb",
            "rm_db",
            "datadb",
            "forcesub",
            "bots",
            "otherbots",
            "settings",
            "request",
            "profile",
            "addpremium",
            "delpremium",
            "premiumusers",
        ]
    )
    & not_listening_filter,
    group=1,
)
async def channel_post(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else None
    if user_id and has_active_listener(client, user_id, message.chat.id):
        return

    if not is_admin(client, user_id):
        text = getattr(client, "reply_text", "You are not authorized.")
        return await message.reply(text)

    reply_text = await message.reply_text("Please wait, processing file...", quote=True)
    target_db = getattr(client, "primary_db_channel", client.db)

    try:
        post_message = await message.copy(chat_id=target_db, disable_notification=True)
    except FloodWait as e:
        wait_time = getattr(e, "value", getattr(e, "x", 5))
        await asyncio.sleep(wait_time)
        post_message = await message.copy(chat_id=target_db, disable_notification=True)
    except Exception as e:
        client.LOGGER(__name__, client.name).error(f"Copying file to DB failed: {e}")
        return await reply_text.edit_text(
            "Something went wrong. Could not save the file."
        )

    file_id = generate_random_id()
    await client.mongodb.add_file(file_id, post_message.id, channel_id=target_db)

    link = f"https://t.me/{client.username}?start={file_id}"
    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔁 Share URL", url=f"https://telegram.me/share/url?url={link}"
                )
            ]
        ]
    )

    await reply_text.edit(
        f"<b>ʜᴇʀᴇ ɪꜱ ʏᴏᴜʀ ʟɪɴᴋ :</b>\n\n<code>{link}</code>",
        reply_markup=reply_markup,
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML,
    )

    if not getattr(client, "disable_btn", False):
        try:
            await post_message.edit_reply_markup(reply_markup)
        except Exception:
            pass


@Client.on_message(filters.channel & filters.incoming)
async def new_post(client: Client, message: Message):
    db_channels = getattr(client, "db_channels", {})
    primary_db = getattr(client, "primary_db_channel", client.db)
    if str(message.chat.id) not in db_channels and message.chat.id != primary_db:
        return
    if getattr(client, "disable_btn", False):
        return

    file_id = generate_random_id()
    await client.mongodb.add_file(file_id, message.id, channel_id=message.chat.id)

    link = f"https://t.me/{client.username}?start={file_id}"
    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔁 Share URL", url=f"https://telegram.me/share/url?url={link}"
                )
            ]
        ]
    )

    try:
        await message.edit_reply_markup(reply_markup)
    except Exception:
        pass
