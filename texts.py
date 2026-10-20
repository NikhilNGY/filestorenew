from pyrogram import Client, filters
from pyrogram.errors.pyromod import ListenerTimeout
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup


async def _safe_render(
    client: Client, query: CallbackQuery, text: str, markup: InlineKeyboardMarkup
):
    """Safely edits or sends the message so photo-to-text transitions never fail."""
    try:
        if query.message.photo:
            await query.message.delete()
            return await client.send_message(
                chat_id=query.message.chat.id,
                text=text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        return await query.message.edit_text(
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            return await client.send_message(
                chat_id=query.message.chat.id,
                text=text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception:
            pass


async def texts(client: Client, query: CallbackQuery):
    """Render the Text Configuration menu in Settings Page 2."""
    msg = f"""<blockquote>**✦ Text Configuration:**</blockquote>
**Start Message:**
<pre>{client.messages.get('START', 'Empty')}</pre>
**Force Sub Message:**
<pre>{client.messages.get('FSUB', 'Empty')}</pre>
**About Message:**
<pre>{client.messages.get('ABOUT', 'Empty')}</pre>
**Shortener Message:**
<pre>{client.messages.get('SHORT_MSG', 'Empty')}</pre>
**Custom Caption:**
<pre>{client.messages.get('CAPTION', '{previouscaption}')}</pre>
**Reply Message:**
<pre>{client.reply_text}</pre>"""

    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ꜱᴛᴀʀᴛ ᴛᴇxᴛ", callback_data="start_txt"),
                InlineKeyboardButton("ꜰꜱᴜʙ ᴛᴇxᴛ", callback_data="fsub_txt"),
            ],
            [
                InlineKeyboardButton("ʀᴇᴘʟʏ ᴛᴇxᴛ", callback_data="reply_txt"),
                InlineKeyboardButton("ᴀʙᴏᴜᴛ ᴛᴇxᴛ", callback_data="about_txt"),
            ],
            [
                InlineKeyboardButton("ꜱʜᴏʀᴛ ᴍꜱɢ", callback_data="short_msg_txt"),
                InlineKeyboardButton("ᴄᴀᴘᴛɪᴏɴ", callback_data="caption_txt"),
            ],
            [InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="settings_page_2")],
        ]
    )
    await _safe_render(client, query, msg, reply_markup)


async def update_text_field(client: Client, query: CallbackQuery, key: str, label: str):
    """Prompt the admin for a new text template and persist it in MongoDB."""
    await query.answer()
    await _safe_render(
        client,
        query,
        f"<blockquote>**✦ Edit {label}:**</blockquote>\n"
        f"Send the new **{label}** text in the next 60 seconds (HTML & Markdown supported).\n"
        f"Send `0` to cancel!",
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", callback_data="texts")]]
        ),
    )
    try:
        res = await client.listen(
            user_id=query.from_user.id, filters=filters.text, timeout=60
        )
        raw_text = res.text.strip() if res.text else ""
        if raw_text == "0":
            await texts(client, query)
            return await res.reply(f"__{label} update cancelled!__")

        formatted_text = (
            res.text.html if hasattr(res.text, "html") and res.text.html else res.text
        )

        client.messages[key] = formatted_text
        if key == "REPLY":
            client.reply_text = formatted_text
            if hasattr(client, "mongodb"):
                await client.mongodb.update_bot_setting("reply_text", formatted_text)

        if hasattr(client, "mongodb"):
            await client.mongodb.update_message_setting(key, formatted_text)

        await texts(client, query)
        return await res.reply(
            f"**✅ {label} has been updated and saved to database!**"
        )
    except ListenerTimeout:
        return await _safe_render(
            client,
            query,
            "**⏰ Timeout! Text update cancelled.**",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="texts")]]
            ),
        )
    except Exception as e:
        logger = getattr(client, "LOGGER", None)
        if logger:
            logger(__name__, client.name).error(f"Error updating text field {key}: {e}")
