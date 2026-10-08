import secrets
import string
import asyncio
from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait

def generate_random_id(length: int = 8) -> str:
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

async def is_not_numeric_reply(_, __, message: Message):
    if message.text and message.text.isdigit():
        return False
    return True

not_numeric_filter = filters.create(is_not_numeric_reply)

@Client.on_message(
    filters.private &
    ~filters.command(['start', 'users', 'broadcast', 'batch', 'genlink', 'usage', 'pbroadcast', 'ban', 'unban']) &
    not_numeric_filter
)
async def channel_post(client: Client, message: Message):
    if hasattr(client, 'admins') and message.from_user.id not in client.admins:
        text = getattr(client, 'reply_text', "You are not authorized.")
        return await message.reply(text)

    reply_text = await message.reply_text("Please wait, processing file...", quote=True)

    try:
        post_message = await message.copy(chat_id=client.db, disable_notification=True)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        post_message = await message.copy(chat_id=client.db, disable_notification=True)
    except Exception as e:
        client.LOGGER(__name__, client.name).error(f"Copying file to DB failed: {e}")
        return await reply_text.edit_text("Something went wrong. Could not save the file.")

    file_id = generate_random_id()
    await client.mongodb.add_file(file_id, post_message.id)

    link = f"https://krpicture0.blogspot.com?start={file_id}"
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Share URL", url=f"https://telegram.me/share/url?url={link}")]])

    await reply_text.edit(
        f"<b>ʜᴇʀᴇ ɪꜱ ʏᴏᴜʀ ʟɪɴᴋ :</b>\n\n{link}",
        reply_markup=reply_markup,
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML
    )

    if not client.disable_btn:
        try:
            await post_message.edit_reply_markup(reply_markup)
        except Exception:
            pass

@Client.on_message(filters.channel & filters.incoming)
async def new_post(client: Client, message: Message):
    if message.chat.id not in client.db_channels:
        return
    if client.disable_btn:
        return

    file_id = generate_random_id()
    await client.mongodb.add_file(file_id, message.id)

    link = f"https://krpicture0.blogspot.com?start={file_id}"
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Share URL", url=f"https://telegram.me/share/url?url={link}")]])

    try:
        await message.edit_reply_markup(reply_markup)
    except Exception:
        pass
        
