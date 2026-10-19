from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

# =============================================================== #
#                TEXT CONFIGURATION MENU & EDITORS                #
# =============================================================== #

async def texts(client: Client, query: CallbackQuery):
    msg = f"""<blockquote>**Text Configuration:**</blockquote>
**Start Message:**
<pre>{client.messages.get('START', 'Empty')}</pre>
**Force Sub Message:**
<pre>{client.messages.get('FSUB', 'Empty')}</pre>
**About Message:**
<pre>{client.messages.get('ABOUT', 'Empty')}</pre>
**Reply Message:**
<pre>{client.reply_text}</pre>
    """
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton('ꜱᴛᴀʀᴛ ᴛᴇxᴛ', 'start_txt'), InlineKeyboardButton('ꜰꜱᴜʙ ᴛᴇxᴛ', 'fsub_txt')],
        [InlineKeyboardButton('ʀᴇᴘʟʏ ᴛᴇxᴛ', 'reply_txt'), InlineKeyboardButton('ᴀʙᴏᴜᴛ ᴛᴇxᴛ', 'about_txt')],
        [InlineKeyboardButton('◂ ʙᴀᴄᴋ', 'settings_page_2')]
    ])
    await query.message.edit_text(msg, reply_markup=reply_markup)
    return


async def update_text_field(client: Client, query: CallbackQuery, db_key: str, label: str):
    await query.answer()
    try:
        ask_text = await client.ask(
            query.from_user.id,
            f"Send new **{label}** message in the next 60 seconds, send `0` to cancel or wait 60 seconds!\n\n__Note: You can use both markdown and html formatting!__",
            filters=filters.text,
            timeout=60
        )
    except Exception:
        return

    try:
        text = ask_text.text.html if hasattr(ask_text.text, "html") else ask_text.text
        if text.strip() == '0':
            return await ask_text.reply(f"__{label} has not changed!__")

        if db_key == "REPLY":
            client.reply_text = text
            await client.mongodb.update_bot_setting("reply_text", text)
        else:
            client.messages[db_key] = text
            await client.mongodb.update_message_setting(db_key, text)

        await texts(client, query)
        return await ask_text.reply(f"__{label} has been successfully updated!__")
    except Exception as e:
        logger = getattr(client, "LOGGER", None)
        if logger:
            logger(__name__, client.name).error(e)


@Client.on_callback_query(filters.regex("^start_txt$"))
async def start_txt(client: Client, query: CallbackQuery):
    await update_text_field(client, query, "START", "Start Message")


@Client.on_callback_query(filters.regex("^fsub_txt$"))
async def force_txt(client: Client, query: CallbackQuery):
    await update_text_field(client, query, "FSUB", "Force Sub Message")


@Client.on_callback_query(filters.regex("^about_txt$"))
async def about_txt(client: Client, query: CallbackQuery):
    await update_text_field(client, query, "ABOUT", "About Message")


@Client.on_callback_query(filters.regex("^reply_txt$"))
async def reply_txt(client: Client, query: CallbackQuery):
    await update_text_field(client, query, "REPLY", "Reply Message")
