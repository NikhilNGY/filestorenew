import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import ListenerTimeout
from config import OWNER_ID

# ================================================================================= #
#                             /bots - MULTI-BOT PANEL                               #
# ================================================================================= #

@Client.on_message(filters.command("bots") & filters.private)
async def multi_bot_panel(client: Client, message: Message):
    if message.from_user.id != OWNER_ID and message.from_user.id not in getattr(client, "admins", []):
        return await message.reply("❌ You are not authorized to use this command.")

    # Fetch settings from your database (Replace with your actual DB calls)
    mb_status = await client.mongodb.get_setting("multi_bot_enabled") or False
    ar_status = await client.mongodb.get_setting("auto_rotate_enabled") or False

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Multi bot {'🟢 On' if mb_status else '🔴 Off'}", callback_data="toggle_mb_status")],
        [InlineKeyboardButton(f"Auto rotate bot {'🟢 On' if ar_status else '🔴 Off'}", callback_data="toggle_ar_status")],
        [InlineKeyboardButton("Add bot", callback_data="mb_add_bot"), InlineKeyboardButton("Rem Bot", callback_data="mb_rem_bot")],
        [InlineKeyboardButton("Bot list", callback_data="mb_list_bots")]
    ])
    
    await message.reply(
        "🤖 **Multi-Bot Management Panel**\n\nConfigure your worker bots below:", 
        reply_markup=markup
    )

@Client.on_callback_query(filters.regex(r"^toggle_mb_status$\vert{}^toggle_ar_status$"))
async def toggle_bot_settings(client: Client, query: CallbackQuery):
    if query.data == "toggle_mb_status":
        current = await client.mongodb.get_setting("multi_bot_enabled")
        await client.mongodb.set_setting("multi_bot_enabled", not current)
    else:
        current = await client.mongodb.get_setting("auto_rotate_enabled")
        await client.mongodb.set_setting("auto_rotate_enabled", not current)
    
    # Refresh panel
    await multi_bot_panel(client, query.message)
    await query.message.delete()

@Client.on_callback_query(filters.regex("^mb_list_bots$"))
async def list_worker_bots(client: Client, query: CallbackQuery):
    # Fetch list of dictionaries containing bot tokens/usernames from your DB
    worker_bots = await client.mongodb.get_worker_bots() 
    
    buttons = []
    for bot in worker_bots:
        bot_id = bot['bot_id']
        bot_name = bot.get('username', f"Bot {bot_id}")
        
        # Format: Bot Name | Remove bot
        buttons.append([
            InlineKeyboardButton(f"🤖 {bot_name}", url=f"https://t.me/{bot_name}"),
            InlineKeyboardButton("🗑️ Remove bot", callback_data=f"mb_rem_specific_{bot_id}")
        ])
    
    # Bottom button
    buttons.append([InlineKeyboardButton("⚠️ Remove All bot", callback_data="mb_rem_all")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_bots_panel")])
    
    await query.message.edit_text(
        f"📋 **Connected Worker Bots ({len(worker_bots)}):**", 
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Client.on_callback_query(filters.regex("^back_to_bots_panel$"))
async def back_to_bots(client: Client, query: CallbackQuery):
    await multi_bot_panel(client, query.message)
    await query.message.delete()

# ================================================================================= #
#                         /otherbots - UNIVERSAL REPLY PANEL                        #
# ================================================================================= #

@Client.on_message(filters.command("otherbots") & filters.private)
async def other_bots_panel(client: Client, message: Message):
    if message.from_user.id != OWNER_ID and message.from_user.id not in getattr(client, "admins", []):
        return await message.reply("❌ You are not authorized.")

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Set text", callback_data="ob_set_txt"), InlineKeyboardButton("remove text", callback_data="ob_rem_txt")],
        [InlineKeyboardButton("Set image", callback_data="ob_set_img"), InlineKeyboardButton("remove image", callback_data="ob_rem_img")],
        [InlineKeyboardButton("Set button", callback_data="ob_set_btn"), InlineKeyboardButton("remove button", callback_data="ob_rem_btn")],
        [InlineKeyboardButton("Close", callback_data="close")]
    ])
    
    # Fetch current configs for display
    config = await client.mongodb.get_otherbots_config()
    current_text = config.get("text", "None")
    current_img = "✅ Set" if config.get("image") else "❌ None"
    current_btn = config.get("button_name", "❌ None")
    
    text = (
        "⚙️ **Worker Bots Universal Reply Settings**\n\n"
        f"**Text:** `{current_text[:50]}...`\n"
        f"**Image:** {current_img}\n"
        f"**Button:** `{current_btn}`\n\n"
        "__Changes apply to all connected worker bots immediately.__"
    )
    
    await message.reply(text, reply_markup=markup)

@Client.on_callback_query(filters.regex(r"^ob_(set|rem)_(txt|img|btn)$"))
async def handle_otherbots_settings(client: Client, query: CallbackQuery):
    action, target = query.data.split("_")[1], query.data.split("_")[2]
    
    if action == "rem":
        if target == "txt": await client.mongodb.update_otherbots_config({"text": None})
        elif target == "img": await client.mongodb.update_otherbots_config({"image": None})
        elif target == "btn": await client.mongodb.update_otherbots_config({"button_name": None, "button_url": None})
        
        await query.answer("Removed successfully!", show_alert=True)
        await other_bots_panel(client, query.message)
        return await query.message.delete()

    # Listeners for 'Set' actions
    await query.message.edit_text("Send the new value within 60 seconds. Send /cancel to abort.")
    
    try:
        if target == "txt":
            res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
            if res.text != "/cancel":
                await client.mongodb.update_otherbots_config({"text": res.text})
                
        elif target == "img":
            res = await client.listen(user_id=query.from_user.id, filters=filters.photo | filters.text, timeout=60)
            if res.photo:
                file_id = res.photo.file_id
                await client.mongodb.update_otherbots_config({"image": file_id})
            elif res.text.startswith(("http://", "https://")):
                await client.mongodb.update_otherbots_config({"image": res.text})
                
        elif target == "btn":
            await query.message.edit_text("Send button Name and URL separated by a | \nExample: `Main Channel | https://t.me/yourchannel`")
            res = await client.listen(user_id=query.from_user.id, filters=filters.text, timeout=60)
            if "|" in res.text:
                name, url = res.text.split("|", 1)
                await client.mongodb.update_otherbots_config({
                    "button_name": name.strip(), 
                    "button_url": url.strip()
                })
                
        await query.message.reply("✅ Value updated successfully!")
        await other_bots_panel(client, query.message)
        
    except ListenerTimeout:
        await query.message.reply("❌ Timeout! Try again.")


# ================================================================================= #
#                    WORKER BOT HANDLERS (AUTO-DELETE & REPLIES)                    #
# ================================================================================= #

async def auto_delete_task(message: Message, delay: int = 1800):
    """Deletes a message automatically after `delay` seconds (Default: 30 minutes)"""
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass  # Message might already be deleted or bot lacks permission

# Note: Bind this function dynamically when starting a worker bot in memory
# e.g., worker_client.add_handler(MessageHandler(worker_bot_message_handler, filters.private))
async def worker_bot_message_handler(worker_client: Client, message: Message):
    # 1. 30 Minute Auto-Delete of User's Private Message (1800 seconds)
    asyncio.create_task(auto_delete_task(message, 1800))
    
    text = message.text or ""
    
    # 2. Check if it's a File Store Payload (Redirected traffic)
    if text.startswith("/start ") and len(text.split(" ", 1)[1]) > 5:
        # Pass the payload to the main file retrieval function here
        # E.g., await send_files_to_user(worker_client, message, payload)
        return

    # 3. Normal /start or generic text message -> Trigger /otherbots custom reply
    config = await worker_client.mongodb.get_otherbots_config() # Use your central DB client here
    
    reply_text = config.get("text") or "Hello! I am a file delivery worker bot."
    reply_img = config.get("image")
    btn_name = config.get("button_name")
    btn_url = config.get("button_url")
    
    # Build Button
    markup = None
    if btn_name and btn_url:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(btn_name, url=btn_url)]])
    
    # Send Reply
    try:
        if reply_img:
            bot_reply = await message.reply_photo(
                photo=reply_img, 
                caption=reply_text, 
                reply_markup=markup
            )
        else:
            bot_reply = await message.reply_text(
                text=reply_text, 
                reply_markup=markup,
                disable_web_page_preview=True
            )
        
        # Optional: Auto-delete the bot's reply as well after 30 minutes to keep PM perfectly clean
        asyncio.create_task(auto_delete_task(bot_reply, 1800))
        
    except Exception as e:
        print(f"Failed to send worker bot reply: {e}")
