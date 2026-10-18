import re
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import humanize
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
from plugins.shortner import get_short
from helper.helper_func import (
    get_messages, 
    force_sub, 
    decode, 
    batch_auto_del_notification
)

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def parse_duration(duration_str: str) -> Optional[timedelta]:
    """
    Parse duration strings like '1 day', '2 weeks', '3 months', '1 year' into timedelta.
    Returns None if the format is invalid.
    """
    duration_str = duration_str.lower().strip()
    match = re.match(r'^(\d+)\s*(day|days|week|weeks|month|months|year|years|hour|hours|min|mins|minute|minutes)$', duration_str)
    
    if not match:
        return None
        
    amount = int(match.group(1))
    unit = match.group(2).rstrip('s')
    
    if unit in ('min', 'minute'):
        return timedelta(minutes=amount)
    elif unit == 'hour':
        return timedelta(hours=amount)
    elif unit == 'day':
        return timedelta(days=amount)
    elif unit == 'week':
        return timedelta(weeks=amount)
    elif unit == 'month':
        return timedelta(days=amount * 30)
    elif unit == 'year':
        return timedelta(days=amount * 365)
    
    return None


def get_settings_text(client: Client) -> str:
    """
    Generates the unified settings overview text to avoid code duplication
    between settings pages.
    """
    total_fsub = len(getattr(client, 'fsub_dict', {}))
    request_enabled = sum(1 for data in getattr(client, 'fsub_dict', {}).values() if len(data) > 2 and data[2])
    timer_enabled = sum(1 for data in getattr(client, 'fsub_dict', {}).values() if len(data) > 3 and data[3] > 0)
    
    total_db_channels = len(getattr(client, 'db_channels', {}))
    primary_db = getattr(client, 'primary_db_channel', getattr(client, 'db', 'ɴᴏɴᴇ'))
    
    return f"""<blockquote>✦ sᴇᴛᴛɪɴɢs ᴏғ @{client.username}</blockquote>
›› **ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟs:** `{total_fsub}` (ʀᴇǫᴜᴇsᴛ: {request_enabled}, ᴛɪᴍᴇʀ: {timer_enabled})
›› **ᴅʙ ᴄʜᴀɴɴᴇʟs:** `{total_db_channels}` (ᴘʀɪᴍᴀʀʏ: `{primary_db}`)
›› **ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ᴛɪᴍᴇʀ:** `{client.auto_del}`
›› **ᴘʀᴏᴛᴇᴄᴛ ᴄᴏɴᴛᴇɴᴛ:** `{"✓ ᴛʀᴜᴇ" if client.protect else "✗ ꜰᴀʟsᴇ"}`
›› **ᴅɪsᴀʙʟᴇ ʙᴜᴛᴛᴏɴ:** `{"✓ ᴛʀᴜᴇ" if client.disable_btn else "✗ ꜰᴀʟsᴇ"}`
›› **ʀᴇᴘʟʏ ᴛᴇxᴛ:** `{client.reply_text if client.reply_text else 'ɴᴏɴᴇ'}`
›› **ᴀᴅᴍɪɴs:** `{len(client.admins)}`
›› **sʜᴏʀᴛɴᴇʀ ᴜʀʟ:** `{getattr(client, 'short_url', 'ɴᴏᴛ sᴇᴛ')}`
›› **ᴛᴜᴛᴏʀɪᴀʟ ʟɪɴᴋ:** `{getattr(client, 'tutorial_link', 'ɴᴏᴛ sᴇᴛ')}`
›› **sᴛᴀʀᴛ ᴍᴇssᴀɢᴇ:**
<pre>{client.messages.get('START', 'ᴇᴍᴘᴛʏ')}</pre>
›› **sᴛᴀʀᴛ ɪᴍᴀɢᴇ:** `{bool(client.messages.get('START_PHOTO', ''))}`
›› **ꜰᴏʀᴄᴇ sᴜʙ ᴍᴇssᴀɢᴇ:**
<pre>{client.messages.get('FSUB', 'ᴇᴍᴘᴛʏ')}</pre>
›› **ꜰᴏʀᴄᴇ sᴜʙ ɪᴍᴀɢᴇ:** `{bool(client.messages.get('FSUB_PHOTO', ''))}`
›› **ᴀʙᴏᴜᴛ ᴍᴇssᴀɢᴇ:**
<pre>{client.messages.get('ABOUT', 'ᴇᴍᴘᴛʏ')}</pre>
›› **ʀᴇᴘʟʏ ᴍᴇssᴀɢᴇ:**
<pre>{client.reply_text}</pre>"""


def is_valid_channel_id(text: str) -> bool:
    """Checks if a string is a valid integer (including negative IDs)."""
    return text.lstrip('-').isdigit()


def is_admin(client: Client, user_id: int) -> bool:
    """Checks if a user is an admin or the owner."""
    return user_id == OWNER_ID or user_id in getattr(client, 'admins', [])


# ==============================================================================
# START, REQUEST & PROFILE COMMANDS
# ==============================================================================

@Client.on_message(filters.command('start') & filters.private)
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
        shortner_enabled = getattr(client, 'shortner_enabled', True)

        # 4. Route non-premium users through shortener if enabled
        if not is_user_pro and not is_admin(client, user_id) and not is_short_link and shortner_enabled:
            try:
                short_link = get_short(f"https://t.me/{client.username}?start=yu3elk{base64_string}7", client)
            except Exception as e:
                client.LOGGER(__name__, client.name).warning(f"Shortener failed: {e}")
                return await message.reply("⚠️ **Couldn't generate short link. Please try again later.**")

            short_photo = client.messages.get("SHORT_PIC", "")
            short_caption = client.messages.get("SHORT_MSG", "🔗 **Please verify the link below to access your files.**")
            tutorial_link = getattr(client, 'tutorial_link', "https://t.me/How_to_Download_7x/26")

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

        # 5. Decode base64 and determine source channel & message IDs
        try:
            string = await decode(base64_string)
            argument = string.split("-")
            ids = []
            source_channel_id = None

            primary_db_id = client.db_channel.id if hasattr(client, 'db_channel') and client.db_channel else client.db
            primary_multiplier = abs(primary_db_id)
            db_channels = getattr(client, 'db_channels', {})

            if len(argument) == 3:
                encoded_start = int(argument[1])
                encoded_end = int(argument[2])
                
                if encoded_start % primary_multiplier == 0 and encoded_end % primary_multiplier == 0:
                    source_channel_id = primary_db_id
                    start = encoded_start // primary_multiplier
                    end = encoded_end // primary_multiplier
                    client.LOGGER(__name__, client.name).info(f"Decoded batch from primary channel {source_channel_id}: {start}-{end}")
                else:
                    for channel_id_str in db_channels.keys():
                        channel_id = int(channel_id_str)
                        channel_multiplier = abs(channel_id)
                        
                        if encoded_start % channel_multiplier == 0 and encoded_end % channel_multiplier == 0:
                            source_channel_id = channel_id
                            start = encoded_start // channel_multiplier
                            end = encoded_end // channel_multiplier
                            client.LOGGER(__name__, client.name).info(f"Decoded batch from secondary channel {source_channel_id}: {start}-{end}")
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
            client.LOGGER(__name__, client.name).warning(f"Error decoding base64: {e}")
            return await message.reply("⚠️ **Invalid or expired link.**")

        # 6. Fetch messages from database channels
        temp_msg = await message.reply("⏳ **Wait a sec...**")
        messages = []

        try:
            if source_channel_id:
                try:
                    msgs = await client.get_messages(
                        chat_id=source_channel_id,
                        message_ids=ids
                    )
                    if not isinstance(msgs, list):
                        msgs = [msgs]
                        
                    valid_msgs = [msg for msg in msgs if msg is not None and not getattr(msg, 'empty', False)]
                    messages.extend(valid_msgs)
                    
                    if len(valid_msgs) < len(ids):
                        found_ids = {msg.id for msg in valid_msgs}
                        missing_ids = [mid for mid in ids if mid not in found_ids]
                        if missing_ids:
                            additional_messages = await get_messages(client, missing_ids)
                            messages.extend(additional_messages)
                except Exception as e:
                    client.LOGGER(__name__, client.name).warning(f"Error getting messages from {source_channel_id}: {e}")
                    messages = await get_messages(client, ids)
            else:
                messages = await get_messages(client, ids)
        except Exception as e:
            await temp_msg.edit_text("❌ **Something went wrong while fetching files!**")
            client.LOGGER(__name__, client.name).warning(f"Error getting messages: {e}")
            return

        if not messages:
            return await temp_msg.edit("❌ **Couldn't find the files in the database.**")
        await temp_msg.delete()

        # 7. Copy messages to user chat
        yugen_msgs = []
        custom_caption_template = client.messages.get('CAPTION', '')
        
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

            reply_markup = msg.reply_markup if not client.disable_btn else None

            try:
                copied_msg = await msg.copy(
                    chat_id=message.from_user.id,
                    caption=caption,
                    reply_markup=reply_markup,
                    protect_content=client.protect
                )
                yugen_msgs.append(copied_msg)
            except FloodWait as e:
                await asyncio.sleep(getattr(e, 'value', getattr(e, 'x', 5)))
                copied_msg = await msg.copy(
                    chat_id=message.from_user.id,
                    caption=caption,
                    reply_markup=reply_markup,
                    protect_content=client.protect
                )
                yugen_msgs.append(copied_msg)
            except Exception as e:
                client.LOGGER(__name__, client.name).warning(f"Failed to send message: {e}")

        # 8. Trigger Auto-Delete Timer if configured
        if yugen_msgs and client.auto_del > 0:
            asyncio.create_task(batch_auto_del_notification(
                bot_username=client.username,
                messages=yugen_msgs,
                delay_time=client.auto_del,
                transfer_link=original_payload,
                chat_id=message.from_user.id,
                client=client
            ))
        return

    # 9. Standard /start greeting
    buttons = [
        [InlineKeyboardButton("Help", callback_data="about"), InlineKeyboardButton("Close", callback_data='close')]
    ]
    if is_admin(client, user_id):
        buttons.insert(0, [InlineKeyboardButton("⛩️ ꜱᴇᴛᴛɪɴɢꜱ ⛩️", callback_data="settings")])

    photo = client.messages.get("START_PHOTO", "")
    start_caption = client.messages.get('START', 'Welcome, {mention}').format(
        first=message.from_user.first_name or "",
        last=message.from_user.last_name or "",
        username=None if not message.from_user.username else '@' + message.from_user.username,
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


@Client.on_message(filters.command('request') & filters.private)
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


@Client.on_message(filters.command('profile') & filters.private)
async def my_plan(client: Client, message: Message):
    user_id = message.from_user.id

    if is_admin(client, user_id):
        return await message.reply_text("🔹 **You're my sensei!**\nYou have full administrative privileges.")
    
    is_user_premium = await client.mongodb.is_pro(user_id)

    if is_user_premium:
        expiry_date = await client.mongodb.get_expiry_date(user_id)
        expiry_str = expiry_date.strftime('%Y-%m-%d %H:%M:%S') if expiry_date else "Lifetime / Permanent"
        
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

@Client.on_message(filters.command('addpremium') & filters.private)
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

    # Add or Update Pro Status
    await client.mongodb.add_pro(user_id_to_add, expiry_date)
    await message.reply_text(f"✅ <b>{user_name} (<code>{user_id_to_add}</code>) is now a Premium user {duration_text}!</b>")
    
    try:
        notify_msg = (
            "🎉 <b>Congratulations! Your Premium membership has been activated "
            f"{duration_text}.</b>"
        )
        await client.send_message(user_id_to_add, notify_msg)
    except Exception as e:
        await message.reply_text(f"⚠️ <i>Added to database, but failed to notify the user: {e}</i>")


@Client.on_message(filters.command('delpremium') & filters.private)
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
        await message.reply_text(f"✅ <b>{user_name} (<code>{user_id_to_remove}</code>) has been removed from Premium users!</b>")
        try:
            await client.send_message(
                user_id_to_remove, 
                "<b>⚠️ Your Premium membership has ended.\n\nTo renew your membership, please contact: @SandalwoodHelpBot.</b>"
            )
        except Exception as e:
            await message.reply_text(f"⚠️ <i>Removed from database, but failed to notify the user: {e}</i>")
    else:
        await message.reply_text(f"ℹ️ <b>{user_name} (<code>{user_id_to_remove}</code>) is not currently a Premium user.</b>")


@Client.on_message(filters.command('premiumusers') & filters.private)
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
    
    # Handle long lists safely
    if len(output_text) > 4096:
        output_text = output_text[:4000] + "\n\n<i>...and more.</i>"
        
    await wait_msg.edit_text(output_text, disable_web_page_preview=True)


# ==============================================================================
# SETTINGS CALLBACK QUERIES
# ==============================================================================

@Client.on_callback_query(filters.regex("^settings$"))
async def settings_page_1(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    msg = get_settings_text(client)
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton('ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟꜱ', 'fsub'), InlineKeyboardButton('ᴅʙ ᴄʜᴀɴɴᴇʟꜱ', 'db_channels')],
        [InlineKeyboardButton('ᴀᴅᴍɪɴꜱ', 'admins'), InlineKeyboardButton('ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ', 'auto_del')],
        [InlineKeyboardButton('ʜᴏᴍᴇ', 'home'), InlineKeyboardButton('›› ɴᴇxᴛ', 'settings_page_2')]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^settings_page_2$"))
async def settings_page_2(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    msg = get_settings_text(client)
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton('ᴘʀᴏᴛᴇᴄᴛ ᴄᴏɴᴛᴇɴᴛ', 'protect'), InlineKeyboardButton('ᴘʜᴏᴛᴏs', 'photos')],
        [InlineKeyboardButton('ᴛᴇxᴛs', 'texts'), InlineKeyboardButton('sʜᴏʀᴛɴᴇʀ', 'shortner')],
        [InlineKeyboardButton('‹ ᴘʀᴇᴠ', 'settings'), InlineKeyboardButton('ʜᴏᴍᴇ', 'home')]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^fsub$"))
async def fsub_settings(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    if getattr(client, 'fsub_dict', None):
        channel_list = []
        for channel_id, channel_data in client.fsub_dict.items():
            channel_name = channel_data[0] if channel_data and len(channel_data) > 0 else "Unknown"
            request_status = "✓ ʀᴇǫᴜᴇsᴛ" if len(channel_data) > 2 and channel_data[2] else "✗ ʀᴇǫᴜᴇsᴛ"
            timer_status = f"ᴛɪᴍᴇʀ: {channel_data[3]}ᴍ" if len(channel_data) > 3 and channel_data[3] > 0 else "ᴛɪᴍᴇʀ: ∞"
            channel_list.append(f"• `{channel_name}` (`{channel_id}`) - {request_status}, {timer_status}")
        
        channels_display = "\n".join(channel_list)
    else:
        channels_display = "_ɴᴏ ꜰᴏʀᴄᴇ sᴜʙsᴄʀɪᴘᴛɪᴏɴ ᴄʜᴀɴɴᴇʟs ᴄᴏɴғɪɢᴜʀᴇᴅ_"
    
    msg = f"""<blockquote>✦ ꜰᴏʀᴄᴇ sᴜʙsᴄʀɪᴘᴛɪᴏɴ sᴇᴛᴛɪɴɢs</blockquote>
›› **ᴄᴏɴғɪɢᴜʀᴇᴅ ᴄʜᴀɴɴᴇʟs:**
{channels_display}

__ᴜsᴇ ᴛʜᴇ ᴀᴘᴘʀᴏᴘʀɪᴀᴛᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴀᴅᴅ ᴏʀ ʀᴇᴍᴏᴠᴇ ᴀ ꜰᴏʀᴄᴇ sᴜʙsᴄʀɪᴘᴛɪᴏɴ ᴄʜᴀɴɴᴇʟ ʙᴀsᴇᴅ ᴏɴ ʏᴏᴜʀ ɴᴇᴇᴅs!__"""

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton('›› ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ', 'add_fsub'), InlineKeyboardButton('›› ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟ', 'rm_fsub')],
        [InlineKeyboardButton('‹ ʙᴀᴄᴋ', 'settings')]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^db_channels$"))
async def db_channels_menu(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)
    
    db_channels = getattr(client, 'db_channels', {})
    if db_channels:
        channel_list = []
        for channel_id_str, channel_data in db_channels.items():
            channel_name = channel_data.get('name', 'Unknown')
            is_primary = "✓ ᴘʀɪᴍᴀʀʏ" if channel_data.get('is_primary', False) else "• sᴇᴄᴏɴᴅᴀʀʏ"
            is_active = "✓ ᴀᴄᴛɪᴠᴇ" if channel_data.get('is_active', True) else "✗ ɪɴᴀᴄᴛɪᴠᴇ"
            channel_list.append(f"• `{channel_name}` (`{channel_id_str}`)\n  {is_primary} | {is_active}")
        
        channels_display = "\n\n".join(channel_list)
    else:
        channels_display = "_ɴᴏ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs ᴄᴏɴғɪɢᴜʀᴇᴅ_"
    
    primary_db = getattr(client, 'primary_db_channel', client.db)
    
    msg = f"""<blockquote>✦ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs sᴇᴛᴛɪɴɢs</blockquote>
›› **ᴄᴜʀʀᴇɴᴛ ᴘʀɪᴍᴀʀʏ ᴅʙ:** `{primary_db}`
›› **ᴛᴏᴛᴀʟ ᴅʙ ᴄʜᴀɴɴᴇʟs:** `{len(db_channels)}`

**ᴄᴏɴғɪɢᴜʀᴇᴅ ᴄʜᴀɴɴᴇʟs:**
{channels_display}

__ᴜsᴇ ᴛʜᴇ ᴀᴘᴘʀᴏᴘʀɪᴀᴛᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴍᴀɴᴀɢᴇ ʏᴏᴜʀ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟs!__"""

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton('›› ᴀᴅᴅ ᴅʙ ᴄʜᴀɴɴᴇʟ', 'add_db_channel'), InlineKeyboardButton('›› ʀᴇᴍᴏᴠᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ', 'rm_db_channel')],
        [InlineKeyboardButton('›› sᴇᴛ ᴘʀɪᴍᴀʀʏ', 'set_primary_db'), InlineKeyboardButton('›› sᴛᴀᴛᴜs', 'toggle_db_status')],
        [InlineKeyboardButton('‹ ʙᴀᴄᴋ', 'settings')]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^add_db_channel$"))
async def add_db_channel(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)
    
    await query.answer()
    msg = f"""<blockquote>✦ ᴀᴅᴅ ɴᴇᴡ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ</blockquote>
›› **ᴄᴜʀʀᴇɴᴛ ᴅʙ ᴄʜᴀɴɴᴇʟs:** `{len(getattr(client, 'db_channels', {}))}`

__sᴇɴᴅ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ɪᴅ (ɴᴇɢᴀᴛɪᴠᴇ ɪɴᴛᴇɢᴇʀ ᴠᴀʟᴜᴇ) ᴏғ ᴛʜᴇ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴀᴅᴅ ɪɴ ᴛʜᴇ ɴᴇxᴛ 60 sᴇᴄᴏɴᴅs!__

**ᴇxᴀᴍᴘʟᴇ:** `-1001234567675`
**ɴᴏᴛᴇ:** ᴍᴀᴋᴇ sᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪs ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ!"""
    
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('‹ ᴄᴀɴᴄᴇʟ', 'db_channels')]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        channel_id_text = res.text.strip()
        
        if not is_valid_channel_id(channel_id_text):
            return await query.message.edit_text(
                "**✗ ɪɴᴠᴀʟɪᴅ ᴄʜᴀɴɴᴇʟ ɪᴅ! ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴇɢᴀᴛɪᴠᴇ ɪɴᴛᴇɢᴇʀ.**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('‹ ʙᴀᴄᴋ', 'db_channels')]])
            )
        
        channel_id = int(channel_id_text)
        db_channels = getattr(client, 'db_channels', {})
        
        if str(channel_id) in db_channels:
            return await query.message.edit_text(
                f"**✗ ᴄʜᴀɴɴᴇʟ `{channel_id}` ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴅᴅᴇᴅ ᴀs ᴀ ᴅʙ ᴄʜᴀɴɴᴇʟ!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('‹ ʙᴀᴄᴋ', 'db_channels')]])
            )
        
        try:
            chat = await client.get_chat(channel_id)
            test_msg = await client.send_message(chat_id=channel_id, text="ᴛᴇsᴛɪɴɢ ᴅʙ ᴄʜᴀɴɴᴇʟ ᴀᴄᴄᴇss")
            await test_msg.delete()
            
            channel_data = {
                'name': chat.title,
                'is_primary': len(db_channels) == 0,
                'is_active': True,
                'added_by': query.from_user.id
            }
            
            await client.mongodb.add_db_channel(channel_id, channel_data)
            
            if not hasattr(client, 'db_channels'):
                client.db_channels = {}
            client.db_channels[str(channel_id)] = channel_data
            
            if channel_data['is_primary']:
                client.primary_db_channel = channel_id
                await client.mongodb.set_primary_db_channel(channel_id)
            
            await query.message.edit_text(
                f"""**✓ ᴅᴀᴛᴀʙᴀsᴇ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!**

›› **ᴄʜᴀɴɴᴇʟ:** `{chat.title}`
›› **ɪᴅ:** `{channel_id}`
›› **sᴛᴀᴛᴜs:** {'ᴘʀɪᴍᴀʀʏ' if channel_data['is_primary'] else 'sᴇᴄᴏɴᴅᴀʀʏ'}""", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('‹ ʙᴀᴄᴋ', 'db_channels')]])
            )
        except Exception as e:
            await query.message.edit_text(
                f"""**✗ ᴇʀʀᴏʀ ᴀᴄᴄᴇssɪɴɢ ᴄʜᴀɴɴᴇʟ!**

›› **ᴇʀʀᴏʀ:** `{str(e)}`

**ᴘʟᴇᴀsᴇ ᴍᴀᴋᴇ sᴜʀᴇ:**
• ʙᴏᴛ ɪs ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ
• ᴄʜᴀɴɴᴇʟ ɪᴅ ɪs ᴄᴏʀʀᴇᴄᴛ""", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('‹ ʙᴀᴄᴋ', 'db_channels')]])
            )
    except ListenerTimeout:
        await query.message.edit_text(
            "**✗ ᴛɪᴍᴇᴏᴜᴛ! ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('‹ ʙᴀᴄᴋ', 'db_channels')]])
        )
    except Exception as e:
        await query.message.edit_text(
            f"**✗ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ:** `{str(e)}`", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('‹ ʙᴀᴄᴋ', 'db_channels')]])
        )


@Client.on_callback_query(filters.regex("^rm_db_channel$"))
async def rm_db_channel(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('❌ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)
    
    await query.answer()
    db_channels = getattr(client, 'db_channels', {})
    
    if not db_channels:
        return await query.message.edit_text(
            "**❌ No database channels to remove!**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )
    
    msg = "<blockquote>**Remove Database Channel:**</blockquote>\n**Available Channels:**\n"
    for channel_id_str, channel_data in db_channels.items():
        channel_name = channel_data.get('name', 'Unknown')
        is_primary = " (Primary)" if channel_data.get('is_primary', False) else ""
        msg += f"• `{channel_name}` - `{channel_id_str}`{is_primary}\n"
    
    msg += "\n__Send the channel ID you want to remove in the next 60 seconds!__"
    
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ᴄᴀɴᴄᴇʟ', 'db_channels')]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        channel_id_text = res.text.strip()
        
        if not is_valid_channel_id(channel_id_text):
            return await query.message.edit_text(
                "**❌ Invalid channel ID!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
            )
        
        channel_id = int(channel_id_text)
        if str(channel_id) not in db_channels:
            return await query.message.edit_text(
                f"**❌ Channel `{channel_id}` is not in the DB channels list!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
            )
        
        if db_channels[str(channel_id)].get('is_primary', False) and len(db_channels) > 1:
            return await query.message.edit_text(
                "**❌ Cannot remove primary channel!**\n\n__Please set another channel as primary first.__", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
            )
        
        channel_name = db_channels[str(channel_id)].get('name', 'Unknown')
        await client.mongodb.remove_db_channel(channel_id)
        del client.db_channels[str(channel_id)]
        
        await query.message.edit_text(
            f"**✅ Database channel removed successfully!**\n\n**Removed:** `{channel_name}` (`{channel_id}`)", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )
    except ListenerTimeout:
        await query.message.edit_text(
            "**❌ Timeout! Please try again.**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )
    except Exception as e:
        await query.message.edit_text(
            f"**❌ Error occurred:** `{str(e)}`", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )


@Client.on_callback_query(filters.regex("^set_primary_db$"))
async def set_primary_db(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('❌ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)
    
    await query.answer()
    db_channels = getattr(client, 'db_channels', {})
    
    if not db_channels:
        return await query.message.edit_text(
            "**❌ No database channels available!**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )
    
    msg = "<blockquote>**Set Primary Database Channel:**</blockquote>\n**Available Channels:**\n"
    for channel_id_str, channel_data in db_channels.items():
        channel_name = channel_data.get('name', 'Unknown')
        is_primary = " (Current Primary)" if channel_data.get('is_primary', False) else ""
        msg += f"• `{channel_name}` - `{channel_id_str}`{is_primary}\n"
    
    msg += "\n__Send the channel ID you want to set as primary in the next 60 seconds!__"
    
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ᴄᴀɴᴄᴇʟ', 'db_channels')]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        channel_id_text = res.text.strip()
        
        if not is_valid_channel_id(channel_id_text):
            return await query.message.edit_text(
                "**❌ Invalid channel ID!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
            )
        
        channel_id = int(channel_id_text)
        if str(channel_id) not in db_channels:
            return await query.message.edit_text(
                f"**❌ Channel `{channel_id}` is not in the DB channels list!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
            )
        
        await client.mongodb.set_primary_db_channel(channel_id)
        
        for ch_id, ch_data in client.db_channels.items():
            ch_data['is_primary'] = (int(ch_id) == channel_id)
        
        client.primary_db_channel = channel_id
        client.db = channel_id
        
        channel_name = db_channels[str(channel_id)].get('name', 'Unknown')
        await query.message.edit_text(
            f"**✅ Primary database channel updated!**\n\n**New Primary:** `{channel_name}` (`{channel_id}`)", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )
    except ListenerTimeout:
        await query.message.edit_text(
            "**❌ Timeout! Please try again.**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )
    except Exception as e:
        await query.message.edit_text(
            f"**❌ Error occurred:** `{str(e)}`", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )


@Client.on_callback_query(filters.regex("^toggle_db_status$"))
async def toggle_db_status(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('❌ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)
    
    await query.answer()
    db_channels = getattr(client, 'db_channels', {})
    
    if not db_channels:
        return await query.message.edit_text(
            "**❌ No database channels available!**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )
    
    msg = "<blockquote>**Toggle Channel Status:**</blockquote>\n**Available Channels:**\n"
    for channel_id_str, channel_data in db_channels.items():
        channel_name = channel_data.get('name', 'Unknown')
        status = "🟢 ᴀᴄᴛɪᴠᴇ" if channel_data.get('is_active', True) else "🔴 ɪɴᴀᴄᴛɪᴠᴇ"
        msg += f"• `{channel_name}` - `{channel_id_str}` ({status})\n"
    
    msg += "\n__Send the channel ID you want to toggle active/inactive status for in the next 60 seconds!__"
    
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ᴄᴀɴᴄᴇʟ', 'db_channels')]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        channel_id_text = res.text.strip()
        
        if not is_valid_channel_id(channel_id_text):
            return await query.message.edit_text(
                "**❌ Invalid channel ID!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
            )
        
        channel_id = int(channel_id_text)
        if str(channel_id) not in db_channels:
            return await query.message.edit_text(
                f"**❌ Channel `{channel_id}` is not in the DB channels list!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
            )
        
        new_status = await client.mongodb.toggle_db_channel_status(channel_id)
        if new_status is not None:
            client.db_channels[str(channel_id)]['is_active'] = new_status
            channel_name = db_channels[str(channel_id)].get('name', 'Unknown')
            status_text = "🟢 Active" if new_status else "🔴 Inactive"
            await query.message.edit_text(
                f"**✅ Channel status updated!**\n\n**Channel:** `{channel_name}` (`{channel_id}`)\n**New Status:** {status_text}", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
            )
        else:
            await query.message.edit_text(
                "**❌ Failed to toggle channel status!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
            )
    except ListenerTimeout:
        await query.message.edit_text(
            "**❌ Timeout! Please try again.**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )
    except Exception as e:
        await query.message.edit_text(
            f"**❌ Error occurred:** `{str(e)}`", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'db_channels')]])
        )


@Client.on_callback_query(filters.regex("^admins$"))
async def admins_menu(client: Client, query: CallbackQuery):
    if query.from_user.id != OWNER_ID:
        return await query.answer('This can only be used by the owner.', show_alert=True)
        
    admin_list_str = ", ".join(f"`{a}`" for a in client.admins) if client.admins else "None"
    msg = f"""<blockquote>**Admin Settings:**</blockquote>
**Admin User IDs:** {admin_list_str}

__Use the appropriate button below to add or remove an admin based on your needs!__"""

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton('ᴀᴅᴅ ᴀᴅᴍɪɴ', 'add_admin'), InlineKeyboardButton('ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴ', 'rm_admin')],
        [InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'settings')]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^photos$"))
async def photos_menu(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    msg = f"""<blockquote>**Photo Settings:**</blockquote>
**Start Photo:** `{client.messages.get("START_PHOTO", "None") or "None"}`
**Force Sub Photo:** `{client.messages.get('FSUB_PHOTO', 'None') or "None"}`

__Use the appropriate button below to set, change, or remove bot photos!__"""

    reply_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                ('ꜱᴇᴛ' if not client.messages.get("START_PHOTO") else 'ᴄʜᴀɴɢᴇ') + '\nꜱᴛᴀʀᴛ ᴘʜᴏᴛᴏ', 
                callback_data='add_start_photo'
            ),
            InlineKeyboardButton(
                ('ꜱᴇᴛ' if not client.messages.get("FSUB_PHOTO") else 'ᴄʜᴀɴɢᴇ') + '\nꜰꜱᴜʙ ᴘʜᴏᴛᴏ', 
                callback_data='add_fsub_photo'
            )
        ],
        [
            InlineKeyboardButton('ʀᴇᴍᴏᴠᴇ\nꜱᴛᴀʀᴛ ᴘʜᴏᴛᴏ', callback_data='rm_start_photo'),
            InlineKeyboardButton('ʀᴇᴍᴏᴠᴇ\nꜰꜱᴜʙ ᴘʜᴏᴛᴏ', callback_data='rm_fsub_photo')
        ],
        [InlineKeyboardButton('◂ ʙᴀᴄᴋ', callback_data='settings_page_2')]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex("^protect$"))
async def toggle_protect(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    client.protect = not client.protect
    await query.answer(f"Protect Content set to: {client.protect}")
    return await settings_page_2(client, query)


@Client.on_callback_query(filters.regex("^auto_del$"))
async def auto_del_menu(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    msg = f"""<blockquote>**Change Auto Delete Time:**</blockquote>
**Current Timer:** `{client.auto_del}` seconds

__Enter a new integer value for the auto delete timer (in seconds). Keep `0` to disable auto delete, or `-1` to keep as is. Timeout in 60 seconds!__"""

    await query.answer()
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ᴄᴀɴᴄᴇʟ', 'settings')]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
        timer_str = res.text.strip()
        
        if timer_str.lstrip('+-').isdigit():
            timer = int(timer_str)
            if timer >= 0:
                client.auto_del = timer
                return await query.message.edit_text(
                    f'**✅ Auto Delete timer value changed to `{timer}` seconds!**', 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'settings')]])
                )
            else:
                return await query.message.edit_text(
                    "**ℹ️ No changes made to the auto delete timer!**", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'settings')]])
                )
        else:
            return await query.message.edit_text(
                "**❌ This is not a valid integer value!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'settings')]])
            )
    except ListenerTimeout:
        return await query.message.edit_text(
            "**❌ Timeout, try again!**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'settings')]])
        )


@Client.on_callback_query(filters.regex("^texts$"))
async def texts_menu(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    msg = f"""<blockquote>**Text Configuration:**</blockquote>
**Start Message:**
<pre>{client.messages.get('START', 'Empty')}</pre>
**Force Sub Message:**
<pre>{client.messages.get('FSUB', 'Empty')}</pre>
**About Message:**
<pre>{client.messages.get('ABOUT', 'Empty')}</pre>
**Reply Message:**
<pre>{client.reply_text}</pre>"""

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton('ꜱᴛᴀʀᴛ ᴛᴇxᴛ', 'start_txt'), InlineKeyboardButton('ꜰꜱᴜʙ ᴛᴇxᴛ', 'fsub_txt')],
        [InlineKeyboardButton('ʀᴇᴘʟʏ ᴛᴇxᴛ', 'reply_txt'), InlineKeyboardButton('ᴀʙᴏᴜᴛ ᴛᴇxᴛ', 'about_txt')],
        [InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'settings_page_2')]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)


@Client.on_callback_query(filters.regex('^rm_start_photo$'))
async def rm_start_photo(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    client.messages['START_PHOTO'] = ''
    await query.answer("Start photo removed!")
    await photos_menu(client, query)


@Client.on_callback_query(filters.regex('^rm_fsub_photo$'))
async def rm_fsub_photo(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    client.messages['FSUB_PHOTO'] = ''
    await query.answer("Force Sub photo removed!")
    await photos_menu(client, query)


@Client.on_callback_query(filters.regex("^add_start_photo$"))
async def add_start_photo(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    msg = f"""<blockquote>**Change Start Image:**</blockquote>
**Current Start Image:** `{client.messages.get('START_PHOTO', '')}`

__Enter a new link (starting with http:// or https://) or send the photo directly within 60 seconds!__"""

    await query.answer()
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ᴄᴀɴᴄᴇʟ', 'photos')]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=(filters.text | filters.photo), timeout=60)
        if res.text and res.text.startswith(('https://', 'http://')):
            client.messages['START_PHOTO'] = res.text.strip()
            return await query.message.edit_text(
                "**✅ This link has been set as the Start photo!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'photos')]])
            )
        elif res.photo:
            loc = await res.download()
            client.messages['START_PHOTO'] = loc
            return await query.message.edit_text(
                "**✅ This image has been set as the Start photo!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'photos')]])
            )
        else:
            return await query.message.edit_text(
                "**❌ Invalid Photo or Link format!**\n__If you're sending the link of an image, it must start with either 'http://' or 'https://'!__", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'photos')]])
            )
    except ListenerTimeout:
        return await query.message.edit_text(
            "**❌ Timeout, try again!**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'photos')]])
        )


@Client.on_callback_query(filters.regex("^add_fsub_photo$"))
async def add_fsub_photo(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer('✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!', show_alert=True)

    msg = f"""<blockquote>**Change Force Sub Image:**</blockquote>
**Current Force Sub Image:** `{client.messages.get('FSUB_PHOTO', '')}`

__Enter a new link (starting with http:// or https://) or send the photo directly within 60 seconds!__"""

    await query.answer()
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ᴄᴀɴᴄᴇʟ', 'photos')]]))
    try:
        res = await client.listen(user_id=query.from_user.id, filters=(filters.text | filters.photo), timeout=60)
        if res.text and res.text.startswith(('https://', 'http://')):
            client.messages['FSUB_PHOTO'] = res.text.strip()
            return await query.message.edit_text(
                "**✅ This link has been set as the Force Sub photo!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'photos')]])
            )
        elif res.photo:
            loc = await res.download()
            client.messages['FSUB_PHOTO'] = loc
            return await query.message.edit_text(
                "**✅ This image has been set as the Force Sub photo!**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'photos')]])
            )
        else:
            return await query.message.edit_text(
                "**❌ Invalid Photo or Link format!**\n__If you're sending the link of an image, it must start with either 'http://' or 'https://'!__", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'photos')]])
            )
    except ListenerTimeout:
        return await query.message.edit_text(
            "**❌ Timeout, try again!**", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'photos')]])
        )
