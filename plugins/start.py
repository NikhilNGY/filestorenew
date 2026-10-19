import re
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from pyrogram.errors import FloodWait
from pyrogram.errors.pyromod import ListenerTimeout

from config import MSG_EFFECT, OWNER_ID
from texts import texts as render_texts_menu, update_text_field
from database.database import (
    get_messages,
    force_sub,
    decode,
    get_short,
    batch_auto_del_notification
)


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def parse_duration(duration_str: str) -> Optional[timedelta]:
    """Parse duration strings like '1 day', '2 weeks', '3 months', '1 year' into timedelta."""
    duration_str = duration_str.lower().strip()
    match = re.match(
        r"^(\d+)\s*(day|days|week|weeks|month|months|year|years|hour|hours|min|mins|minute|minutes)$",
        duration_str
    )
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2).rstrip("s")

    if unit in ("min", "minute"):
        return timedelta(minutes=amount)
    elif unit == "hour":
        return timedelta(hours=amount)
    elif unit == "day":
        return timedelta(days=amount)
    elif unit == "week":
        return timedelta(weeks=amount)
    elif unit == "month":
        return timedelta(days=amount * 30)
    elif unit == "year":
        return timedelta(days=amount * 365)
    return None


def get_settings_text(client: Client) -> str:
    """Generates the unified settings overview text across both settings pages."""
    total_fsub = len(getattr(client, "fsub_dict", {}))
    request_enabled = sum(1 for data in getattr(client, "fsub_dict", {}).values() if len(data) > 2 and data[2])
    timer_enabled = sum(1 for data in getattr(client, "fsub_dict", {}).values() if len(data) > 3 and data[3] > 0)

    total_db_channels = len(getattr(client, "db_channels", {}))
    primary_db = getattr(client, "primary_db_channel", getattr(client, "db", "ɴᴏɴᴇ"))
    multidb_status = "✓ ᴏɴ" if getattr(client, "multi_db_enabled", True) else "✗ ᴏꜰꜰ"
    multidb_count = len(getattr(client, "db_urls", []))
    worker_count = len(getattr(client, "worker_clients", {}))
    shortner_status = "✓ ᴏɴ" if getattr(client, "shortner_enabled", False) else "✗ ᴏꜰꜰ"

    return f"""<blockquote>✦ sᴇᴛᴛɪɴɢs ᴏғ @{client.username}</blockquote>
›› **ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟs:** `{total_fsub}` (ʀᴇǫᴜᴇsᴛ: {request_enabled}, ᴛɪᴍᴇʀ: {timer_enabled})
›› **ᴅʙ ᴄʜᴀɴɴᴇʟs:** `{total_db_channels}` (ᴘʀɪᴍᴀʀʏ: `{primary_db}`)
›› **ᴍᴜʟᴛɪ-ᴅᴀᴛᴀᴅʙ:** `{multidb_status}` (`{multidb_count}` ᴜʀʟs)
›› **ᴡᴏʀᴋᴇʀ ʙᴏᴛs:** `{worker_count}` ᴄᴏɴɴᴇᴄᴛᴇᴅ
›› **ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ᴛɪᴍᴇʀ:** `{client.auto_del}s`
›› **ᴘʀᴏᴛᴇᴄᴛ ᴄᴏɴᴛᴇɴᴛ:** `{"✓ ᴛʀᴜᴇ" if client.protect else "✗ ꜰᴀʟsᴇ"}`
›› **ᴅɪsᴀʙʟᴇ ʙᴜᴛᴛᴏɴ:** `{"✓ ᴛʀᴜᴇ" if client.disable_btn else "✗ ꜰᴀʟsᴇ"}`
›› **ᴀᴅᴍɪɴs:** `{len(client.admins)}`
›› **sʜᴏʀᴛɴᴇʀ:** `{shortner_status}` (`{getattr(client, 'short_url', '') or 'ɴᴏᴛ sᴇᴛ'}`)
›› **ᴛᴜᴛᴏʀɪᴀʟ ʟɪɴᴋ:** `{getattr(client, 'tutorial_link', 'ɴᴏᴛ sᴇᴛ')}`
›› **sᴛᴀʀᴛ ɪᴍᴀɢᴇ:** `{bool(client.messages.get('START_PHOTO', ''))}`
›› **ꜰᴏʀᴄᴇ sᴜʙ ɪᴍᴀɢᴇ:** `{bool(client.messages.get('FSUB_PHOTO', ''))}`"""


def is_valid_channel_id(text: str) -> bool:
    """Checks if a string is a valid integer (including negative IDs)."""
    return text.lstrip("-").isdigit()


def is_admin(client: Client, user_id: int) -> bool:
    """Checks if a user is an admin or the owner."""
    return user_id == OWNER_ID or user_id in getattr(client, "admins", [])


async def resolve_and_deliver_files(
    delivery_client: Client,
    main_client: Client,
    message: Message,
    base64_string: str,
    original_payload: str
):
    """
    Shared file resolution and delivery engine used by both the main bot
    and connected worker bots (/bots).
    """
    ids = []
    source_channel_id = None

    # 1. Check if payload is a direct random file_id stored via MongoDB (/datadb)
    file_doc = await main_client.mongodb.get_file(base64_string)
    if file_doc:
        ids = [int(file_doc["message_id"])]
        source_channel_id = file_doc.get("channel_id") or getattr(main_client, "primary_db_channel", main_client.db)
    else:
        # 2. Decode base64 batch / genlink payload
        try:
            string = await decode(base64_string)
            argument = string.split("-")

            primary_db_id = (
                main_client.db_channel.id
                if hasattr(main_client, "db_channel") and main_client.db_channel
                else main_client.db
            )
            primary_multiplier = abs(primary_db_id)
            db_channels = getattr(main_client, "db_channels", {})

            if len(argument) == 3:
                encoded_start = int(argument[1])
                encoded_end = int(argument[2])

                if encoded_start % primary_multiplier == 0 and encoded_end % primary_multiplier == 0:
                    source_channel_id = primary_db_id
                    start = encoded_start // primary_multiplier
                    end = encoded_end // primary_multiplier
                else:
                    for channel_id_str in db_channels.keys():
                        channel_id = int(channel_id_str)
                        channel_multiplier = abs(channel_id)
                        if encoded_start % channel_multiplier == 0 and encoded_end % channel_multiplier == 0:
                            source_channel_id = channel_id
                            start = encoded_start // channel_multiplier
                            end = encoded_end // channel_multiplier
                            break

                    if source_channel_id is None:
                        source_channel_id = primary_db_id
                        start = encoded_start // primary_multiplier
                        end = encoded_end // primary_multiplier

                ids = list(range(start, end + 1)) if start <= end else list(range(start, end - 1, -1))

            elif len(argument) == 2:
                encoded_msg = int(argument[1])
                if encoded_msg % primary_multiplier == 0:
                    source_channel_id = primary_db_id
                    ids = [encoded_msg // primary_multiplier]
                else:
                    for channel_id_str in db_channels.keys():
                        channel_id = int(channel_id_str)
                        channel_multiplier = abs(channel_id)
                        if encoded_msg % channel_multiplier == 0:
                            source_channel_id = channel_id
                            ids = [encoded_msg // channel_multiplier]
                            break

                    if source_channel_id is None:
                        source_channel_id = primary_db_id
                        ids = [encoded_msg // primary_multiplier]
        except Exception as e:
            main_client.LOGGER(__name__, main_client.name).warning(f"Error decoding payload: {e}")
            return await message.reply("⚠️ **Invalid or expired link.**")

    if not ids:
        return await message.reply("⚠️ **Invalid or expired link.**")

    temp_msg = await message.reply("⏳ **Wait a sec...**")
    messages = []

    try:
        if source_channel_id:
            try:
                msgs = await delivery_client.get_messages(
                    chat_id=source_channel_id,
                    message_ids=ids
                )
                if not isinstance(msgs, list):
                    msgs = [msgs]
                valid_msgs = [msg for msg in msgs if msg is not None and not getattr(msg, "empty", False)]
                messages.extend(valid_msgs)

                if len(valid_msgs) < len(ids):
                    found_ids = {msg.id for msg in valid_msgs}
                    missing_ids = [mid for mid in ids if mid not in found_ids]
                    if missing_ids:
                        additional_messages = await get_messages(main_client, missing_ids)
                        messages.extend(additional_messages)
            except Exception:
                messages = await get_messages(main_client, ids)
        else:
            messages = await get_messages(main_client, ids)
    except Exception as e:
        await temp_msg.edit_text("❌ **Something went wrong while fetching files!**")
        main_client.LOGGER(__name__, main_client.name).warning(f"Error getting messages: {e}")
        return

    if not messages:
        return await temp_msg.edit("❌ **Couldn't find the files in the database.**")
    await temp_msg.delete()

    yugen_msgs = []
    custom_caption_template = main_client.messages.get("CAPTION", "")

    for msg in messages:
        original_caption = msg.caption.html if msg.caption else ""
        file_name = msg.document.file_name if msg.document else original_caption

        if custom_caption_template and (msg.document or msg.video or msg.audio):
            try:
                caption = custom_caption_template.format(previouscaption=original_caption or file_name)
            except Exception:
                caption = original_caption
        else:
            caption = original_caption

        reply_markup = msg.reply_markup if not main_client.disable_btn else None

        try:
            copied_msg = await msg.copy(
                chat_id=message.from_user.id,
                caption=caption,
                reply_markup=reply_markup,
                protect_content=main_client.protect
            )
            yugen_msgs.append(copied_msg)
        except FloodWait as e:
            await asyncio.sleep(getattr(e, "value", getattr(e, "x", 5)))
            copied_msg = await msg.copy(
                chat_id=message.from_user.id,
                caption=caption,
                reply_markup=reply_markup,
                protect_content=main_client.protect
            )
            yugen_msgs.append(copied_msg)
        except Exception as e:
            main_client.LOGGER(__name__, main_client.name).warning(f"Failed to send message: {e}")

    if yugen_msgs and main_client.auto_del > 0:
        bot_uname = getattr(delivery_client, "username", main_client.username)
        asyncio.create_task(
            batch_auto_del_notification(
                bot_username=bot_uname,
                messages=yugen_msgs,
                delay_time=main_client.auto_del,
                transfer_link=original_payload,
                chat_id=message.from_user.id,
                client=delivery_client
            )
        )


# ==============================================================================
# START, REQUEST & PROFILE COMMANDS
# ==============================================================================

@Client.on_message(filters.command("start") & filters.private)
@force_sub
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id

    # 1. Add user to DB if not present
    if not await client.mongodb.present_user(user_id):
        try:
            await client.mongodb.add_user(user_id)
        except Exception as e:
            client.LOGGER(__name__, client.name).warning(f"Error adding user {user_id}:\n{e}")

    # 2. Check if user is banned
    if await client.mongodb.is_banned(user_id):
        return await message.reply("**🚫 You have been banned from using this bot!**")

    text = message.text

    # Handle Deep-Linking Payloads
    if len(text) > 7:
        try:
            original_payload = text.split(" ", 1)[1]
            base64_string = original_payload

            is_short_link = False
            if base64_string.startswith("yu3elk"):
                base64_string = base64_string[6:-1]
                is_short_link = True
        except IndexError:
            return await message.reply("⚠️ **Invalid command format.**")

        # 3. Check premium status & shortener configuration
        is_user_pro = await client.mongodb.is_pro(user_id)
        shortner_enabled = getattr(client, "shortner_enabled", False)

        # 4. Route non-premium users through shortener if enabled
        if not is_user_pro and not is_admin(client, user_id) and not is_short_link and shortner_enabled:
            try:
                short_link = await get_short(f"https://t.me/{client.username}?start=yu3elk{base64_string}7", client)
            except Exception as e:
                client.LOGGER(__name__, client.name).warning(f"Shortener failed: {e}")
                return await message.reply("⚠️ **Couldn't generate short link. Please try again later.**")

            short_photo = client.messages.get("SHORT_PIC", "")
            short_caption = client.messages.get(
                "SHORT_MSG",
                "🔗 **Please verify the link below to access your files.**"
            ).format(
                first=message.from_user.first_name or "",
                last=message.from_user.last_name or "",
                username=None if not message.from_user.username else "@" + message.from_user.username,
                mention=message.from_user.mention,
                id=message.from_user.id
            )
            tutorial_link = getattr(client, "tutorial_link", "https://t.me/How_to_Download_7x/26")

            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("• ᴏᴘᴇɴ ʟɪɴᴋ", url=short_link),
                    InlineKeyboardButton("ᴛᴜᴛᴏʀɪᴀʟ •", url=tutorial_link)
                ],
                [
                    InlineKeyboardButton("• ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ •", url="https://t.me/Premium_Fliix/21")
                ]
            ])

            if short_photo:
                await client.send_photo(
                    chat_id=message.chat.id,
                    photo=short_photo,
                    caption=short_caption,
                    reply_markup=markup
                )
            else:
                await message.reply(short_caption, reply_markup=markup)
            return

        # 5. If Multi-Bot (/bots) is enabled and worker bots are running, redirect or rotate
        mb_enabled = await client.mongodb.get_setting("multi_bot_enabled", False)
        if mb_enabled and getattr(client, "worker_clients", {}):
            from plugins.others import pick_worker_bot
            worker_client = await pick_worker_bot(client)
            if worker_client and getattr(worker_client, "username", None):
                redirect_url = f"https://t.me/{worker_client.username}?start={base64_string}"
                return await message.reply(
                    "🚀 **Click the button below to get your files instantly from our delivery bot:**",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📥 ɢᴇᴛ ꜰɪʟᴇs ɴᴏᴡ", url=redirect_url)]
                    ])
                )

        # 6. Deliver files directly via main bot
        await resolve_and_deliver_files(client, client, message, base64_string, original_payload)
        return

    # 7. Standard /start greeting
    buttons = [
        [InlineKeyboardButton("Help", callback_data="about"), InlineKeyboardButton("Close", callback_data="close")]
    ]
    if is_admin(client, user_id):
        buttons.insert(0, [InlineKeyboardButton("⛩️ ꜱᴇᴛᴛɪɴɢꜱ ⛩️", callback_data="settings")])

    photo = client.messages.get("START_PHOTO", "")
    start_caption = client.messages.get("START", "Welcome, {mention}").format(
        first=message.from_user.first_name or "",
        last=message.from_user.last_name or "",
        username=None if not message.from_user.username else "@" + message.from_user.username,
        mention=message.from_user.mention,
        id=message.from_user.id
    )

    if photo:
        await client.send_photo(
            chat_id=message.chat.id,
            photo=photo,
            caption=start_caption,
            message_effect_id=MSG_EFFECT,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await client.send_message(
            chat_id=message.chat.id,
            text=start_caption,
            message_effect_id=MSG_EFFECT,
            reply_markup=InlineKeyboardMarkup(buttons)
        )


@Client.on_message(filters.command("request") & filters.private)
async def request_command(client: Client, message: Message):
    user_id = message.from_user.id

    if is_admin(client, user_id):
        return await message.reply_text("🔹 **You are my sensei!**\nThis command is only for regular users.")

    if not await client.mongodb.is_pro(user_id):
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Upgrade to Premium", url="https://t.me/hanime_arena/5")]
        ])
        return await message.reply(
            "❌ **You are not a premium user.**\nUpgrade to premium to access the request feature.",
            reply_markup=reply_markup
        )

    if len(message.command) < 2:
        return await message.reply("⚠️ **Send me your request in this format:**\n`/request Your_Request_Here`")

    requested = " ".join(message.command[1:])
    owner_message = (
        f"📩 **New Request from {message.from_user.mention}**\n\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"📝 **Request:** `{requested}`"
    )

    try:
        await client.send_message(OWNER_ID, owner_message)
        await message.reply("✅ **Thanks for your request!**\nYour request has been forwarded and will be reviewed soon.")
    except Exception as e:
        client.LOGGER(__name__, client.name).warning(f"Failed to forward request to owner: {e}")
        await message.reply("⚠️ **Could not send request right now. Please try again later.**")


@Client.on_message(filters.command("profile") & filters.private)
async def my_plan(client: Client, message: Message):
    user_id = message.from_user.id

    if is_admin(client, user_id):
        return await message.reply_text("🔹 **You're my sensei!**\nYou have full administrative privileges.")

    is_user_premium = await client.mongodb.is_pro(user_id)

    if is_user_premium:
        expiry_date = await client.mongodb.get_expiry_date(user_id)
        expiry_str = expiry_date.strftime("%Y-%m-%d %H:%M:%S") if expiry_date else "Lifetime / Permanent"

        await message.reply_text(
            "**👤 Profile Information:**\n\n"
            f"🔸 **User ID:** `{user_id}`\n"
            "🔸 **Plan:** `Premium 🌟`\n"
            "🔸 **Ads:** `Disabled`\n"
            "🔸 **Requests:** `Enabled`\n"
            f"🔸 **Expires:** `{expiry_str}`\n\n"
            "🎉 *Thank you for being a Premium subscriber!*"
        )
    else:
        await message.reply_text(
            "**👤 Profile Information:**\n\n"
            f"🔸 **User ID:** `{user_id}`\n"
            "🔸 **Plan:** `Free`\n"
            "🔸 **Ads:** `Enabled`\n"
            "🔸 **Requests:** `Disabled`\n\n"
            "🔓 **Unlock Premium to get more benefits!**\n"
            "👉 Contact: @GetoPro"
        )


# ==============================================================================
# PREMIUM USER MANAGEMENT (PRO_USERS)
# ==============================================================================

@Client.on_message(filters.command("addpremium") & filters.private)
async def add_premium_command(client: Client, message: Message):
    if message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ **Only the Owner can use this command!**")

    usage = (
        "<b>⚠️ Usage:</b>\n"
        "<code>/addpremium &lt;userid&gt; [duration]</code>\n\n"
        "<b>Examples:</b>\n"
        "• <code>/addpremium 123456 1 day</code>\n"
        "• <code>/addpremium 123456 2 weeks</code>\n"
        "• <code>/addpremium 123456 1 month</code>\n"
        "• <code>/addpremium 123456 1 year</code>\n"
        "• <code>/addpremium 123456</code> (for permanent premium)"
    )

    parts = message.command[1:]
    if not parts:
        return await message.reply_text(usage)

    try:
        user_id_to_add = int(parts[0])
    except ValueError:
        return await message.reply_text("❌ **Invalid user ID. Please provide a valid numeric ID.**")

    try:
        user = await client.get_users(user_id_to_add)
        user_name = f"{user.first_name} {user.last_name}".strip() if user.last_name else user.first_name
    except Exception:
        user_name = f"User ({user_id_to_add})"

    duration_str = " ".join(parts[1:]) if len(parts) > 1 else None
    expiry_date = None
    duration_text = "permanently"

    if duration_str:
        duration = parse_duration(duration_str)
        if not duration:
            return await message.reply_text(f"❌ **Invalid duration format.**\n\n{usage}")
        expiry_date = datetime.now() + duration
        duration_text = f"until {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}"

    await client.mongodb.add_pro(user_id_to_add, expiry_date)
    await message.reply_text(
        f"✅ <b>{user_name} (<code>{user_id_to_add}</code>) is now a Premium user {duration_text}!</b>"
    )

    try:
        notify_msg = f"🎉 <b>Congratulations! Your Premium membership has been activated {duration_text}.</b>"
        await client.send_message(user_id_to_add, notify_msg)
    except Exception as e:
        await message.reply_text(f"⚠️ <i>Added to database, but failed to notify the user: {e}</i>")


@Client.on_message(filters.command("delpremium") & filters.private)
async def remove_premium_command(client: Client, message: Message):
    if message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ **Only the Owner can use this command!**")

    if len(message.command) != 2:
        return await message.reply_text("⚠️ <b>Usage:</b> <code>/delpremium &lt;userid&gt;</code>")

    try:
        user_id_to_remove = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ **Invalid user ID. Please check again!**")

    try:
        user = await client.get_users(user_id_to_remove)
        user_name = f"{user.first_name} {user.last_name}".strip() if user.last_name else user.first_name
    except Exception:
        user_name = f"User ({user_id_to_remove})"

    if await client.mongodb.is_pro(user_id_to_remove):
        await client.mongodb.remove_pro(user_id_to_remove)
        await message.reply_text(
            f"✅ <b>{user_name} (<code>{user_id_to_remove}</code>) has been removed from Premium users!</b>"
        )
        try:
            await client.send_message(
                user_id_to_remove,
                "<b>⚠️ Your Premium membership has ended.\n\nTo renew your membership, please contact support.</b>"
            )
        except Exception as e:
            await message.reply_text(f"⚠️ <i>Removed from database, but failed to notify the user: {e}</i>")
    else:
        await message.reply_text(
            f"ℹ️ <b>{user_name} (<code>{user_id_to_remove}</code>) is not currently a Premium user.</b>"
        )


@Client.on_message(filters.command("premiumusers") & filters.private)
async def premium_list_command(client: Client, message: Message):
    if message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ **Only the Owner can use this command!**")

    wait_msg = await message.reply_text("⏳ **Fetching premium users list...**")
    pro_user_ids = await client.mongodb.get_pros_list()

    if not pro_user_ids:
        return await wait_msg.edit_text("ℹ️ <b>No premium users found in the database.</b>")

    formatted_users = []
    for idx, user_id in enumerate(pro_user_ids, start=1):
        expiry_date = await client.mongodb.get_expiry_date(user_id)
        status = f"Expires: {expiry_date.strftime('%Y-%m-%d %H:%M')}" if expiry_date else "Permanent"

        try:
            user = await client.get_users(user_id)
            full_name = f"{user.first_name} {user.last_name}".strip() if user.last_name else user.first_name
            username = f"@{user.username}" if user.username else "No Username"
            formatted_users.append(f"{idx}. <b>{full_name}</b> (<code>{user_id}</code>) - {username} [{status}]")
        except Exception:
            formatted_users.append(f"{idx}. <b>Unknown User</b> (<code>{user_id}</code>) [{status}]")

    output_text = "<b>📊 Premium Users List:</b>\n\n" + "\n".join(formatted_users)
    if len(output_text) > 4096:
        output_text = output_text[:4000] + "\n\n<i>...and more.</i>"

    await wait_msg.edit_text(output_text, disable_web_page_preview=True)


# ==============================================================================
# SETTINGS CALLBACK QUERIES (WITH BOTS, OTHERBOTS, DATADB & ALL BUTTONS)
# ==============================================================================

@Client.on_message(filters.command("settings") & filters.private)
async def settings_command(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply(client.reply_text)
    msg = get_settings_text(client)
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟꜱ", "fsub"), InlineKeyboardButton("ᴅʙ ᴄʜᴀɴɴᴇʟꜱ", "db_channels")],
        [InlineKeyboardButton("🗄️ ᴅᴀᴛᴀᴅʙ (ᴍᴜʟᴛɪ-ᴅʙ)", "datadb_menu"), InlineKeyboardButton("🤖 ᴍᴜʟᴛɪ-ʙᴏᴛꜱ", "bots_menu")],
        [InlineKeyboardButton("ᴀᴅᴍɪɴꜱ", "admins"), InlineKeyboardButton("ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ", "auto_del")],
        [InlineKeyboardButton("ʜᴏᴍᴇ", "home"), InlineKeyboardButton("›› ɴᴇxᴛ", "settings_page_2")]
    ])
    await message.reply_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^settings$"))
async def settings_page_1(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    msg = get_settings_text(client)
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟꜱ", "fsub"), InlineKeyboardButton("ᴅʙ ᴄʜᴀɴɴᴇʟꜱ", "db_channels")],
        [InlineKeyboardButton("🗄️ ᴅᴀᴛᴀᴅʙ (ᴍᴜʟᴛɪ-ᴅʙ)", "datadb_menu"), InlineKeyboardButton("🤖 ᴍᴜʟᴛɪ-ʙᴏᴛꜱ", "bots_menu")],
        [InlineKeyboardButton("ᴀᴅᴍɪɴꜱ", "admins"), InlineKeyboardButton("ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ", "auto_del")],
        [InlineKeyboardButton("ʜᴏᴍᴇ", "home"), InlineKeyboardButton("›› ɴᴇxᴛ", "settings_page_2")]
    ])
    if query.message.photo:
        await query.message.delete()
        await client.send_message(query.message.chat.id, msg, reply_markup=reply_markup)
    else:
        await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^settings_page_2$"))
async def settings_page_2(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    msg = get_settings_text(client)
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("ᴘʀᴏᴛᴇᴄᴛ ᴄᴏɴᴛᴇɴᴛ", "protect"), InlineKeyboardButton("ᴅɪsᴀʙʟᴇ ʙᴜᴛᴛᴏɴ", "toggle_disable_btn")],
        [InlineKeyboardButton("ᴘʜᴏᴛᴏs", "photos"), InlineKeyboardButton("ᴛᴇxᴛs", "texts")],
        [InlineKeyboardButton("🔗 sʜᴏʀᴛɴᴇʀ", "shortner"), InlineKeyboardButton("⚙️ ᴏᴛʜᴇʀʙᴏᴛs ʀᴇᴘʟʏ", "otherbots_menu")],
        [InlineKeyboardButton("‹ ᴘʀᴇᴠ", "settings"), InlineKeyboardButton("ʜᴏᴍᴇ", "home")]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


# ==============================================================================
# DB CHANNELS SETTINGS CALLBACKS
# ==============================================================================

@Client.on_callback_query(filters.regex("^db_channels$"))
async def db_channels_menu(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    db_channels = getattr(client, "db_channels", {})
    if db_channels:
        channel_list = []
        for channel_id_str, channel_data in db_channels.items():
            channel_name = channel_data.get("name", "Unknown")
            is_primary = "✓ ᴘʀɪᴍᴀʀʏ" if channel_data.get("is_primary", False) else "• sᴇᴄᴏɴᴅᴀʀʏ"
            is_active = "✓ ᴀᴄᴛɪᴠᴇ" if channel_data.get("is_active", True) else "✗ ɪɴᴀᴄᴛɪᴠᴇ"
            channel_list.append(f"• `{channel_name}` (`{channel_id_str}`)\n  {is_primary} | {is_active}")
        channels_display = "\n\n".join(channel_list)
    else:
        channels_display = "_ɴᴏ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs ᴄᴏɴғɪɢᴜʀᴇᴅ_"

    primary_db = getattr(client, "primary_db_channel", client.db)

    msg = f"""<blockquote>✦ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs sᴇᴛᴛɪɴɢs</blockquote>
›› **ᴄᴜʀʀᴇɴᴛ ᴘʀɪᴍᴀʀʏ ᴅʙ:** `{primary_db}`
›› **ᴛᴏᴛᴀʟ ᴅʙ ᴄʜᴀɴɴᴇʟs:** `{len(db_channels)}`

**ᴄᴏɴғɪɢᴜʀᴇᴅ ᴄʜᴀɴɴᴇʟs:**
{channels_display}

__ᴜsᴇ ᴛʜᴇ ᴀᴘᴘʀᴏᴘʀɪᴀᴛᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴍᴀɴᴀɢᴇ ʏᴏᴜʀ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs!__"""

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("›› ᴀᴅᴅ ᴅʙ ᴄʜᴀɴɴᴇʟ", "add_db_channel"), InlineKeyboardButton("›› ʀᴇᴍᴏᴠᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ", "rm_db_channel")],
        [InlineKeyboardButton("›› sᴇᴛ ᴘʀɪᴍᴀʀʏ", "set_primary_db"), InlineKeyboardButton("›› sᴛᴀᴛᴜs", "toggle_db_status")],
        [InlineKeyboardButton("›› ᴠɪᴇᴡ ᴅᴇᴛᴀɪʟs", "db_details")],
        [InlineKeyboardButton("‹ ʙᴀᴄᴋ", "settings")]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^add_db_channel$"))
async def add_db_channel(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    await query.answer()
    msg = f"""<blockquote>✦ ᴀᴅᴅ ɴᴇᴡ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ</blockquote>
›› **ᴄᴜʀʀᴇɴᴛ ᴅʙ ᴄʜᴀɴɴᴇʟs:** `{len(getattr(client, 'db_channels', {}))}`

__sᴇɴᴅ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ɪᴅ (ɴᴇɢᴀᴛɪᴠᴇ ɪɴᴛᴇɢᴇʀ ᴠᴀʟᴜᴇ) ᴏғ ᴛʜᴇ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴀᴅᴅ ɪɴ ᴛʜᴇ ɴᴇxᴛ 60 sᴇᴄᴏɴᴅs!__

**ᴇxᴀᴍᴘʟᴇ:** `-1001234567675`
**ɴᴏᴛᴇ:** ᴍᴀᴋᴇ sᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪs ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ!"""

    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ ᴄᴀɴᴄᴇʟ", "db_channels")]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        channel_id_text = res.text.strip()

        if not is_valid_channel_id(channel_id_text):
            return await query.message.edit_text(
                "**✗ ɪɴᴠᴀʟɪᴅ ᴄʜᴀɴɴᴇʟ ɪᴅ! ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴇɢᴀᴛɪᴠᴇ ɪɴᴛᴇɢᴇʀ.**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ ʙᴀᴄᴋ", "db_channels")]])
            )

        channel_id = int(channel_id_text)
        db_channels = getattr(client, "db_channels", {})

        if str(channel_id) in db_channels:
            return await query.message.edit_text(
                f"**✗ ᴄʜᴀɴɴᴇʟ `{channel_id}` ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴅᴅᴇᴅ ᴀs ᴀ ᴅʙ ᴄʜᴀɴɴᴇʟ!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ ʙᴀᴄᴋ", "db_channels")]])
            )

        try:
            chat = await client.get_chat(channel_id)
            test_msg = await client.send_message(chat_id=channel_id, text="ᴛᴇsᴛɪɴɢ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴀᴄᴄᴇss")
            await test_msg.delete()

            channel_data = {
                "name": chat.title,
                "is_primary": len(db_channels) == 0,
                "is_active": True,
                "added_by": query.from_user.id
            }

            await client.mongodb.add_db_channel(channel_id, channel_data)
            if not hasattr(client, "db_channels"):
                client.db_channels = {}
            client.db_channels[str(channel_id)] = channel_data

            if channel_data["is_primary"]:
                client.primary_db_channel = channel_id
                await client.mongodb.set_primary_db_channel(channel_id)

            await query.message.edit_text(
                f"""**✓ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!**

›› **ᴄʜᴀɴɴᴇʟ:** `{chat.title}`
›› **ɪᴅ:** `{channel_id}`
›› **sᴛᴀᴛᴜs:** {'ᴘʀɪᴍᴀʀʏ' if channel_data['is_primary'] else 'sᴇᴄᴏɴᴅᴀʀʏ'}""",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ ʙᴀᴄᴋ", "db_channels")]])
            )
        except Exception as e:
            await query.message.edit_text(
                f"**✗ ᴇʀʀᴏʀ ᴀᴄᴄᴇssɪɴɢ ᴄʜᴀɴɴᴇʟ:** `{str(e)}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ ʙᴀᴄᴋ", "db_channels")]])
            )
    except ListenerTimeout:
        await query.message.edit_text(
            "**✗ ᴛɪᴍᴇᴏᴜᴛ! ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ ʙᴀᴄᴋ", "db_channels")]])
        )


@Client.on_callback_query(filters.regex("^rm_db_channel$"))
async def rm_db_channel(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("❌ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    await query.answer()
    db_channels = getattr(client, "db_channels", {})
    if not db_channels:
        return await query.message.edit_text(
            "**❌ No database channels to remove!**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
        )

    msg = "<blockquote>**Remove Database Channel:**</blockquote>\n**Available Channels:**\n"
    for channel_id_str, channel_data in db_channels.items():
        channel_name = channel_data.get("name", "Unknown")
        is_primary = " (Primary)" if channel_data.get("is_primary", False) else ""
        msg += f"• `{channel_name}` - `{channel_id_str}`{is_primary}\n"
    msg += "\n__Send the channel ID you want to remove in the next 60 seconds!__"

    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", "db_channels")]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        channel_id_text = res.text.strip()

        if not is_valid_channel_id(channel_id_text):
            return await query.message.edit_text(
                "**❌ Invalid channel ID!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
            )

        channel_id = int(channel_id_text)
        if str(channel_id) not in db_channels:
            return await query.message.edit_text(
                f"**❌ Channel `{channel_id}` is not in the DB channels list!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
            )

        if db_channels[str(channel_id)].get("is_primary", False) and len(db_channels) > 1:
            return await query.message.edit_text(
                "**❌ Cannot remove primary channel! Set another channel as primary first.**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
            )

        channel_name = db_channels[str(channel_id)].get("name", "Unknown")
        await client.mongodb.remove_db_channel(channel_id)
        del client.db_channels[str(channel_id)]

        await query.message.edit_text(
            f"**✅ Database channel removed successfully!**\n\n**Removed:** `{channel_name}` (`{channel_id}`)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
        )
    except ListenerTimeout:
        await query.message.edit_text(
            "**❌ Timeout! Please try again.**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
        )


@Client.on_callback_query(filters.regex("^set_primary_db$"))
async def set_primary_db(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("❌ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    await query.answer()
    db_channels = getattr(client, "db_channels", {})
    if not db_channels:
        return await query.message.edit_text(
            "**❌ No database channels available!**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
        )

    msg = "<blockquote>**Set Primary Database Channel:**</blockquote>\n**Available Channels:**\n"
    for channel_id_str, channel_data in db_channels.items():
        channel_name = channel_data.get("name", "Unknown")
        is_primary = " (Current Primary)" if channel_data.get("is_primary", False) else ""
        msg += f"• `{channel_name}` - `{channel_id_str}`{is_primary}\n"
    msg += "\n__Send the channel ID you want to set as primary in the next 60 seconds!__"

    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", "db_channels")]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        channel_id_text = res.text.strip()

        if not is_valid_channel_id(channel_id_text):
            return await query.message.edit_text(
                "**❌ Invalid channel ID!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
            )

        channel_id = int(channel_id_text)
        if str(channel_id) not in db_channels:
            return await query.message.edit_text(
                f"**❌ Channel `{channel_id}` is not in the DB channels list!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
            )

        await client.mongodb.set_primary_db_channel(channel_id)
        for ch_id, ch_data in client.db_channels.items():
            ch_data["is_primary"] = (int(ch_id) == channel_id)

        client.primary_db_channel = channel_id
        client.db = channel_id

        channel_name = db_channels[str(channel_id)].get("name", "Unknown")
        await query.message.edit_text(
            f"**✅ Primary database channel updated!**\n\n**New Primary:** `{channel_name}` (`{channel_id}`)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
        )
    except ListenerTimeout:
        await query.message.edit_text(
            "**❌ Timeout! Please try again.**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
        )


@Client.on_callback_query(filters.regex("^toggle_db_status$"))
async def toggle_db_status(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("❌ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    await query.answer()
    db_channels = getattr(client, "db_channels", {})
    if not db_channels:
        return await query.message.edit_text(
            "**❌ No database channels available!**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
        )

    msg = "<blockquote>**Toggle Channel Status:**</blockquote>\n**Available Channels:**\n"
    for channel_id_str, channel_data in db_channels.items():
        channel_name = channel_data.get("name", "Unknown")
        status = "🟢 ᴀᴄᴛɪᴠᴇ" if channel_data.get("is_active", True) else "🔴 ɪɴᴀᴄᴛɪᴠᴇ"
        msg += f"• `{channel_name}` - `{channel_id_str}` ({status})\n"
    msg += "\n__Send the channel ID you want to toggle active/inactive status for in the next 60 seconds!__"

    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", "db_channels")]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        channel_id_text = res.text.strip()

        if not is_valid_channel_id(channel_id_text):
            return await query.message.edit_text(
                "**❌ Invalid channel ID!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
            )

        channel_id = int(channel_id_text)
        if str(channel_id) not in db_channels:
            return await query.message.edit_text(
                f"**❌ Channel `{channel_id}` is not in the DB channels list!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
            )

        new_status = await client.mongodb.toggle_db_channel_status(channel_id)
        if new_status is not None:
            client.db_channels[str(channel_id)]["is_active"] = new_status
            channel_name = db_channels[str(channel_id)].get("name", "Unknown")
            status_text = "🟢 Active" if new_status else "🔴 Inactive"
            await query.message.edit_text(
                f"**✅ Channel status updated!**\n\n**Channel:** `{channel_name}` (`{channel_id}`)\n**New Status:** {status_text}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
            )
    except ListenerTimeout:
        await query.message.edit_text(
            "**❌ Timeout! Please try again.**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "db_channels")]])
        )


# ==============================================================================
# PROTECT, DISABLE BUTTON, AUTO DELETE, PHOTOS, TEXTS & SHORTENER SETTINGS
# ==============================================================================

@Client.on_callback_query(filters.regex("^protect$"))
async def toggle_protect(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    client.protect = not client.protect
    await client.mongodb.update_bot_setting("protect", client.protect)
    await query.answer(f"Protect Content set to: {client.protect}")
    return await settings_page_2(client, query)


@Client.on_callback_query(filters.regex("^toggle_disable_btn$"))
async def toggle_disable_btn_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    client.disable_btn = not client.disable_btn
    await client.mongodb.update_bot_setting("disable_btn", client.disable_btn)
    await query.answer(f"Disable Channel Button set to: {client.disable_btn}")
    return await settings_page_2(client, query)


@Client.on_callback_query(filters.regex("^auto_del$"))
async def auto_del_menu(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    msg = f"""<blockquote>**Change Auto Delete Time:**</blockquote>
**Current Timer:** `{client.auto_del}` seconds

__Enter a new integer value for the auto delete timer (in seconds). Keep `0` to disable auto delete, or `-1` to keep as is. Timeout in 60 seconds!__"""

    await query.answer()
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", "settings")]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        timer_str = res.text.strip()

        if timer_str.lstrip("+-").isdigit():
            timer = int(timer_str)
            if timer >= 0:
                client.auto_del = timer
                await client.mongodb.update_bot_setting("auto_del", timer)
                return await query.message.edit_text(
                    f"**✅ Auto Delete timer value changed to `{timer}` seconds!**",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "settings")]])
                )
            else:
                return await query.message.edit_text(
                    "**ℹ️ No changes made to the auto delete timer!**",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "settings")]])
                )
        else:
            return await query.message.edit_text(
                "**❌ This is not a valid integer value!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "settings")]])
            )
    except ListenerTimeout:
        return await query.message.edit_text(
            "**❌ Timeout, try again!**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "settings")]])
        )


@Client.on_callback_query(filters.regex("^photos$"))
async def photos_menu(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    msg = f"""<blockquote>**Photo Settings:**</blockquote>
**Start Photo:** `{client.messages.get("START_PHOTO", "None") or "None"}`
**Force Sub Photo:** `{client.messages.get("FSUB_PHOTO", "None") or "None"}`
**Shortener Photo:** `{client.messages.get("SHORT_PIC", "None") or "None"}`

__Use the appropriate button below to set, change, or remove bot photos!__"""

    reply_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                ("ꜱᴇᴛ" if not client.messages.get("START_PHOTO") else "ᴄʜᴀɴɢᴇ") + "\nꜱᴛᴀʀᴛ ᴘʜᴏᴛᴏ",
                callback_data="add_start_photo"
            ),
            InlineKeyboardButton(
                ("ꜱᴇᴛ" if not client.messages.get("FSUB_PHOTO") else "ᴄʜᴀɴɢᴇ") + "\nꜰꜱᴜʙ ᴘʜᴏᴛᴏ",
                callback_data="add_fsub_photo"
            )
        ],
        [
            InlineKeyboardButton("ʀᴇᴍᴏᴠᴇ\nꜱᴛᴀʀᴛ ᴘʜᴏᴛᴏ", callback_data="rm_start_photo"),
            InlineKeyboardButton("ʀᴇᴍᴏᴠᴇ\nꜰꜱᴜʙ ᴘʜᴏᴛᴏ", callback_data="rm_fsub_photo")
        ],
        [InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="settings_page_2")]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^rm_start_photo$"))
async def rm_start_photo(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    client.messages["START_PHOTO"] = ""
    await client.mongodb.update_message_setting("START_PHOTO", "")
    await query.answer("Start photo removed!")
    await photos_menu(client, query)


@Client.on_callback_query(filters.regex("^rm_fsub_photo$"))
async def rm_fsub_photo(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    client.messages["FSUB_PHOTO"] = ""
    await client.mongodb.update_message_setting("FSUB_PHOTO", "")
    await query.answer("Force Sub photo removed!")
    await photos_menu(client, query)


@Client.on_callback_query(filters.regex("^add_start_photo$"))
async def add_start_photo(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    msg = f"""<blockquote>**Change Start Image:**</blockquote>
**Current Start Image:** `{client.messages.get('START_PHOTO', '')}`

__Enter a new link (starting with http:// or https://) or send the photo directly within 60 seconds!__"""

    await query.answer()
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", "photos")]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=(filters.text | filters.photo), timeout=60)
        if res.text and res.text.startswith(("https://", "http://")):
            client.messages["START_PHOTO"] = res.text.strip()
            await client.mongodb.update_message_setting("START_PHOTO", client.messages["START_PHOTO"])
            return await query.message.edit_text(
                "**✅ This link has been set as the Start photo!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "photos")]])
            )
        elif res.photo:
            client.messages["START_PHOTO"] = res.photo.file_id
            await client.mongodb.update_message_setting("START_PHOTO", res.photo.file_id)
            return await query.message.edit_text(
                "**✅ This image has been set as the Start photo!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "photos")]])
            )
        else:
            return await query.message.edit_text(
                "**❌ Invalid Photo or Link format!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "photos")]])
            )
    except ListenerTimeout:
        return await query.message.edit_text(
            "**❌ Timeout, try again!**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "photos")]])
        )


@Client.on_callback_query(filters.regex("^add_fsub_photo$"))
async def add_fsub_photo(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    msg = f"""<blockquote>**Change Force Sub Image:**</blockquote>
**Current Force Sub Image:** `{client.messages.get('FSUB_PHOTO', '')}`

__Enter a new link (starting with http:// or https://) or send the photo directly within 60 seconds!__"""

    await query.answer()
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", "photos")]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=(filters.text | filters.photo), timeout=60)
        if res.text and res.text.startswith(("https://", "http://")):
            client.messages["FSUB_PHOTO"] = res.text.strip()
            await client.mongodb.update_message_setting("FSUB_PHOTO", client.messages["FSUB_PHOTO"])
            return await query.message.edit_text(
                "**✅ This link has been set as the Force Sub photo!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "photos")]])
            )
        elif res.photo:
            client.messages["FSUB_PHOTO"] = res.photo.file_id
            await client.mongodb.update_message_setting("FSUB_PHOTO", res.photo.file_id)
            return await query.message.edit_text(
                "**✅ This image has been set as the Force Sub photo!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "photos")]])
            )
        else:
            return await query.message.edit_text(
                "**❌ Invalid Photo or Link format!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "photos")]])
            )
    except ListenerTimeout:
        return await query.message.edit_text(
            "**❌ Timeout, try again!**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "photos")]])
        )


@Client.on_callback_query(filters.regex("^texts$"))
async def texts_menu(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)
    await render_texts_menu(client, query)


@Client.on_callback_query(filters.regex("^(start_txt|fsub_txt|reply_txt|about_txt|short_msg_txt|caption_txt)$"))
async def edit_text_settings(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    key_map = {
        "start_txt": ("START", "Start Message"),
        "fsub_txt": ("FSUB", "Force Sub Message"),
        "reply_txt": ("REPLY", "Reply Message"),
        "about_txt": ("ABOUT", "About Message"),
        "short_msg_txt": ("SHORT_MSG", "Shortener Message"),
        "caption_txt": ("CAPTION", "Custom Caption Template")
    }
    db_key, label = key_map[query.data]
    await update_text_field(client, query, db_key, label)


@Client.on_callback_query(filters.regex("^shortner$"))
async def shortner_settings_menu(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    enabled = getattr(client, "shortner_enabled", False)
    status_str = "🟢 ON" if enabled else "🔴 OFF"
    short_url = getattr(client, "short_url", "") or "Not Set"
    short_api = getattr(client, "short_api", "") or "Not Set"
    tutorial = getattr(client, "tutorial_link", "https://t.me/How_to_Download_7x/26")

    msg = f"""<blockquote>**🔗 Shortener Settings:**</blockquote>
›› **Status:** `{status_str}`
›› **Shortener Domain:** `{short_url}`
›› **API Key:** `{short_api}`
›› **Tutorial Link:** `{tutorial}`

__Configure your link shortener settings using the buttons below:__"""

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Shortener: {status_str}", callback_data="shortner_toggle")],
        [
            InlineKeyboardButton("🌐 Set Domain", callback_data="shortner_set_url"),
            InlineKeyboardButton("🔑 Set API", callback_data="shortner_set_api")
        ],
        [InlineKeyboardButton("🎬 Set Tutorial Link", callback_data="shortner_set_tut")],
        [InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="settings_page_2")]
    ])
    await query.message.edit_text(msg, reply_markup=markup)


@Client.on_callback_query(filters.regex("^shortner_toggle$"))
async def shortner_toggle_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    client.shortner_enabled = not getattr(client, "shortner_enabled", False)
    await client.mongodb.set_shortner_status(client.shortner_enabled)
    await query.answer(f"Shortener {'Enabled ✅' if client.shortner_enabled else 'Disabled ❌'}")
    await shortner_settings_menu(client, query)


@Client.on_callback_query(filters.regex("^shortner_set_(url|api|tut)$"))
async def shortner_set_values_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    field = query.data.split("_")[-1]
    prompts = {
        "url": ("short_url", "Send the **Shortener Domain** (e.g. `linkshortify.com`):"),
        "api": ("short_api", "Send the **Shortener API Key**:"),
        "tut": ("tutorial_link", "Send the **Tutorial Link** (e.g. `https://t.me/channel/10`):")
    }
    attr_name, prompt_text = prompts[field]
    await query.answer()
    await query.message.edit_text(
        prompt_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", "shortner")]])
    )
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        val = res.text.strip()
        setattr(client, attr_name, val)
        await client.mongodb.update_shortner_setting(attr_name, val)
        await query.message.edit_text(
            f"**✅ Updated `{attr_name}` to:** `{val}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "shortner")]])
        )
    except ListenerTimeout:
        await query.message.edit_text(
            "**❌ Timeout, try again!**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◂ ʙᴀᴄᴋ", "shortner")]])
        )
