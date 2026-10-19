from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatJoinRequest,
    ChatMemberUpdated
)
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors.pyromod import ListenerTimeout

from database.database import is_bot_admin
from config import OWNER_ID


def is_admin(client: Client, user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in getattr(client, "admins", [])


def _has_fsub_channel(client: Client, channel_id: int) -> bool:
    fsub_dict = getattr(client, "fsub_dict", {})
    return int(channel_id) in fsub_dict or str(channel_id) in fsub_dict


# =============================================================== #
#                JOIN REQUEST & MEMBER HANDLERS                   #
# =============================================================== #

@Client.on_chat_join_request(filters.channel)
async def handle_join_request(client: Client, join_request: ChatJoinRequest):
    """Handle join request for fsub channels."""
    if not getattr(client, "fsub_enabled", True):
        return

    user_id = join_request.from_user.id
    channel_id = int(join_request.chat.id)

    if not _has_fsub_channel(client, channel_id):
        return

    if await client.mongodb.is_banned(user_id):
        return

    try:
        if not await client.mongodb.present_user(user_id):
            await client.mongodb.add_user(user_id)

        await client.mongodb.add_join_request(user_id, channel_id, getattr(join_request, "id", None))
        await client.mongodb.update_fsub_status(user_id, channel_id, "request_submitted")
        await client.mongodb.add_channel_user(channel_id, user_id)
    except Exception as e:
        client.LOGGER(__name__, client.name).error(f"Join request error: {user_id} in {channel_id}: {e}")


@Client.on_chat_member_updated(filters.channel)
async def handle_member_update(client: Client, chat_member_updated: ChatMemberUpdated):
    """Handle member status updates for fsub channels."""
    if not getattr(client, "fsub_enabled", True):
        return

    if not chat_member_updated.from_user:
        return

    user_id = chat_member_updated.from_user.id
    channel_id = int(chat_member_updated.chat.id)

    if not _has_fsub_channel(client, channel_id):
        return

    old_status = chat_member_updated.old_chat_member.status if chat_member_updated.old_chat_member else None
    new_status = chat_member_updated.new_chat_member.status if chat_member_updated.new_chat_member else None

    try:
        if not await client.mongodb.present_user(user_id):
            await client.mongodb.add_user(user_id)

        active_statuses = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}

        if new_status in active_statuses and (old_status is None or old_status not in active_statuses):
            await client.mongodb.update_fsub_status(user_id, channel_id, "joined")
            await client.mongodb.add_channel_user(channel_id, user_id)

            if await client.mongodb.has_submitted_join_request(user_id, channel_id):
                await client.mongodb.update_join_request_status(user_id, channel_id, "approved")

        elif old_status in active_statuses and (new_status is None or new_status not in active_statuses):
            await client.mongodb.update_fsub_status(user_id, channel_id, "left")
            await client.mongodb.remove_channel_user(channel_id, user_id)

            if await client.mongodb.has_submitted_join_request(user_id, channel_id):
                await client.mongodb.remove_join_request(user_id, channel_id)

        elif new_status == ChatMemberStatus.BANNED:
            await client.mongodb.update_fsub_status(user_id, channel_id, "banned")
            await client.mongodb.remove_channel_user(channel_id, user_id)

            if await client.mongodb.has_submitted_join_request(user_id, channel_id):
                await client.mongodb.remove_join_request(user_id, channel_id)

        elif new_status == ChatMemberStatus.RESTRICTED:
            await client.mongodb.update_fsub_status(user_id, channel_id, "restricted")
            await client.mongodb.add_channel_user(channel_id, user_id)

    except Exception as e:
        client.LOGGER(__name__, client.name).error(f"Member update error: {user_id} in {channel_id}: {e}")


# =============================================================== #
#                FORCE SUBSCRIBE MENU & HELPERS                   #
# =============================================================== #

def get_fsub_text_and_markup(client: Client):
    """Generates the Force Sub menu message text and InlineKeyboardMarkup."""
    if not hasattr(client, "fsub_enabled"):
        client.fsub_enabled = True
    if not hasattr(client, "auto_rotate"):
        client.auto_rotate = False
    if not hasattr(client, "fsub_dict"):
        client.fsub_dict = {}
    if not hasattr(client, "req_channels"):
        client.req_channels = []

    fsub_status = "ON ✅" if client.fsub_enabled else "OFF ❌"
    rotate_status = "ON ✅" if client.auto_rotate else "OFF ❌"

    if client.fsub_dict:
        channel_list = []
        for channel_id, channel_data in client.fsub_dict.items():
            channel_name = channel_data[0] if channel_data and len(channel_data) > 0 else "Unknown"
            request_status = "Request: ✅" if len(channel_data) > 2 and channel_data[2] else "Request: ❌"
            timer_status = f"Timer: {channel_data[3]}m" if len(channel_data) > 3 and channel_data[3] > 0 else "Timer: ∞"
            channel_list.append(f"• `{channel_name}` (`{channel_id}`) - {request_status}, {timer_status}")

        channels_display = "\n".join(channel_list)
    else:
        channels_display = "_No force subscription channels configured_"

    msg = f"""<blockquote>**✦ Force Subscription Settings:**</blockquote>
›› **Force Subscribe:** `{fsub_status}`
›› **Auto Rotate:** `{rotate_status}`

**Configured Channels ({len(client.fsub_dict)}):**
{channels_display}

__Use the buttons below to manage your force subscription settings and channels!__"""

    reply_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"Force Sub: {fsub_status}", callback_data="toggle_fsub"),
            InlineKeyboardButton(f"Auto Rotate: {rotate_status}", callback_data="toggle_rotate")
        ],
        [
            InlineKeyboardButton("➕ Add FSub", callback_data="add_fsub"),
            InlineKeyboardButton("➖ Rm FSub", callback_data="rm_fsub")
        ],
        [
            InlineKeyboardButton("📋 GetAllFSub", callback_data="getall_fsub"),
            InlineKeyboardButton("🗑️ RmAllFSub", callback_data="rmall_fsub")
        ],
        [
            InlineKeyboardButton("◂ ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="settings")
        ]
    ])
    return msg, reply_markup


@Client.on_message(filters.command("forcesub") & filters.private)
async def forcesub_command(client: Client, message: Message):
    """Handle /forcesub command to display the button menu."""
    if not is_admin(client, message.from_user.id):
        return await message.reply("**⚠️ You are not authorized to use this command!**")

    msg, reply_markup = get_fsub_text_and_markup(client)
    await message.reply_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^fsub$"))
async def fsub(client: Client, query: CallbackQuery):
    """Display or refresh the Force Subscription panel via callback."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    msg, reply_markup = get_fsub_text_and_markup(client)
    try:
        await query.message.edit_text(msg, reply_markup=reply_markup)
    except Exception:
        pass


# =============================================================== #
#                BUTTON CALLBACK HANDLERS                         #
# =============================================================== #

@Client.on_callback_query(filters.regex("^toggle_fsub$"))
async def toggle_fsub_cb(client: Client, query: CallbackQuery):
    """Toggle Force Subscribe On/Off."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    client.fsub_enabled = not getattr(client, "fsub_enabled", True)
    await client.mongodb.set_fsub_status(client.fsub_enabled)

    status_str = "Enabled ✅" if client.fsub_enabled else "Disabled ❌"
    await query.answer(f"Force Subscribe is now {status_str}", show_alert=False)
    await fsub(client, query)


@Client.on_callback_query(filters.regex("^toggle_rotate$"))
async def toggle_rotate_cb(client: Client, query: CallbackQuery):
    """Toggle Auto Rotate On/Off."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    client.auto_rotate = not getattr(client, "auto_rotate", False)
    await client.mongodb.set_auto_rotate(client.auto_rotate)

    status_str = "Enabled ✅" if client.auto_rotate else "Disabled ❌"
    await query.answer(f"Auto Rotate is now {status_str}", show_alert=False)
    await fsub(client, query)


@Client.on_callback_query(filters.regex("^add_fsub$"))
async def add_fsub(client: Client, query: CallbackQuery):
    """Add a new channel to Force Subscribe list."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    await query.answer()
    try:
        ask_channel_info = await client.ask(
            query.from_user.id,
            "Send **channel id** (negative integer), **request boolean** (`yes`/`no`/`true`/`false`), and **timer** (integer in minutes, `0` for no timer) separated by spaces in the next 60 seconds!\n\n"
            "<blockquote expandable>**Example:** `-10089479289 yes 5`\n\n"
            "• `-10089479289` = Force Sub channel ID\n"
            "• `yes` = Join Request link enabled\n"
            "• `5` = Invite link expires after 5 minutes</blockquote>",
            filters=filters.text,
            timeout=60
        )
    except ListenerTimeout:
        return await query.message.reply("**⏰ Timeout! Operation cancelled.**")
    except Exception:
        return

    try:
        channel_info = ask_channel_info.text.split()
        if len(channel_info) != 3:
            return await ask_channel_info.reply("**✗ Invalid format! Please send 3 values: `<channel_id> <request> <timer>`**")

        channel_id_str, request_str, timer_str = channel_info
        channel_id = int(channel_id_str)

        if _has_fsub_channel(client, channel_id):
            return await ask_channel_info.reply("**✗ This channel ID already exists in the Force Sub list! Remove it first to change its configuration.**")

        val, res = await is_bot_admin(client, channel_id)
        if not val:
            return await ask_channel_info.reply(f"**✗ Error:** `{res}`")

        if request_str.lower() in ("true", "on", "yes"):
            request = True
        elif request_str.lower() in ("false", "off", "no"):
            request = False
        else:
            return await ask_channel_info.reply("**✗ Invalid request value! Use `yes` or `no`.**")

        if timer_str.isdigit():
            timer = int(timer_str)
        else:
            return await ask_channel_info.reply("**✗ Timer must be a valid non-negative integer!**")

        chat = await client.get_chat(channel_id)
        name = chat.title

        if timer > 0:
            client.fsub_dict[channel_id] = [name, None, request, timer]
        else:
            chat_link = await client.create_chat_invite_link(channel_id, creates_join_request=request)
            link = chat_link.invite_link
            client.fsub_dict[channel_id] = [name, link, request, timer]

        if request and channel_id not in client.req_channels:
            client.req_channels.append(channel_id)
            await client.mongodb.set_channels(client.req_channels)

        await client.mongodb.add_fsub_channel(channel_id, client.fsub_dict[channel_id])
        await fsub(client, query)
        return await ask_channel_info.reply(f"**✓ Channel `{name.strip()}` (`{channel_id}`) has been added to Force Subscribe!**")
    except Exception as e:
        return await ask_channel_info.reply(f"**✗ Error:** `{e}`")


@Client.on_callback_query(filters.regex("^rm_fsub$"))
async def rm_fsub(client: Client, query: CallbackQuery):
    """Remove a single channel from Force Subscribe list."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    await query.answer()
    try:
        ask_channel_info = await client.ask(
            query.from_user.id,
            "Send the **channel ID** (negative integer value) to remove in the next 60 seconds!",
            filters=filters.text,
            timeout=60
        )
    except ListenerTimeout:
        return await query.message.reply("**⏰ Timeout! Operation cancelled.**")
    except Exception:
        return

    try:
        channel_id = int(ask_channel_info.text.strip())
        if not _has_fsub_channel(client, channel_id):
            return await ask_channel_info.reply("**✗ This channel ID is not in the Force Sub list!**")

        if channel_id in client.req_channels:
            client.req_channels.remove(channel_id)
            await client.mongodb.set_channels(client.req_channels)

        client.fsub_dict.pop(channel_id, None)
        client.fsub_dict.pop(str(channel_id), None)
        await client.mongodb.remove_fsub_channel(channel_id)

        await fsub(client, query)
        return await ask_channel_info.reply(f"**✓ Channel `{channel_id}` has been removed from Force Subscribe!**")
    except Exception as e:
        return await ask_channel_info.reply(f"**✗ Error:** `{e}`")


@Client.on_callback_query(filters.regex("^getall_fsub$"))
async def getall_fsub(client: Client, query: CallbackQuery):
    """Display detailed information for all configured Force Sub channels."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    if not getattr(client, "fsub_dict", {}):
        return await query.answer("No Force Sub channels are currently configured!", show_alert=True)

    await query.answer()
    details = ["**📋 All Force Subscription Channels:**\n"]
    for idx, (cid, data) in enumerate(client.fsub_dict.items(), 1):
        name = data[0] if len(data) > 0 else "Unknown"
        link = data[1] if len(data) > 1 and data[1] else "Dynamic / Timer Link"
        req = "Yes ✅" if len(data) > 2 and data[2] else "No ❌"
        timer = f"{data[3]} mins" if len(data) > 3 and data[3] > 0 else "No Timer (∞)"
        details.append(
            f"**{idx}. {name}**\n"
            f"   • **ID:** `{cid}`\n"
            f"   • **Request Mode:** `{req}`\n"
            f"   • **Timer:** `{timer}`\n"
            f"   • **Invite Link:** {link}\n"
        )

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ ᴛᴏ ꜰsᴜʙ ᴍᴇɴᴜ", callback_data="fsub")]])
    await query.message.edit_text("\n".join(details), reply_markup=markup, disable_web_page_preview=True)


@Client.on_callback_query(filters.regex("^rmall_fsub$"))
async def rmall_fsub(client: Client, query: CallbackQuery):
    """Ask confirmation before removing all Force Sub channels."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    if not getattr(client, "fsub_dict", {}):
        return await query.answer("Force Sub list is already empty!", show_alert=True)

    await query.answer()
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Remove All", callback_data="confirm_rmall_fsub"),
            InlineKeyboardButton("❌ Cancel", callback_data="fsub")
        ]
    ])
    await query.message.edit_text(
        "**⚠️ Are you sure you want to remove ALL Force Subscribe channels?**\n\n__This action will clear all channels from memory and database!__",
        reply_markup=markup
    )


@Client.on_callback_query(filters.regex("^confirm_rmall_fsub$"))
async def confirm_rmall_fsub(client: Client, query: CallbackQuery):
    """Execute removal of all Force Sub channels."""
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    try:
        channel_ids = list(client.fsub_dict.keys())
        for cid in channel_ids:
            try:
                await client.mongodb.remove_fsub_channel(int(cid))
            except Exception:
                pass

        client.fsub_dict.clear()
        if hasattr(client, "req_channels"):
            client.req_channels.clear()
            await client.mongodb.set_channels(client.req_channels)

        await query.answer("✅ All Force Sub channels have been removed!", show_alert=True)
        await fsub(client, query)
    except Exception as e:
        await query.answer(f"Error removing channels: {e}", show_alert=True)
