import asyncio

from pyrogram import Client, filters
from pyrogram.errors.pyromod import ListenerTimeout
from pyrogram.handlers import MessageHandler
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import API_HASH, API_ID, OWNER_ID
from plugins.start import is_admin, resolve_and_deliver_files

# ================================================================================= #
#                     WORKER BOT LIFECYCLE & LOAD BALANCER                          #
# ================================================================================= #

_worker_rr_index = 0


async def auto_delete_task(message: Message, delay: int = 1800):
    """Deletes a message automatically after `delay` seconds (Default: 30 minutes)."""
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass


async def worker_bot_message_handler(worker_client: Client, message: Message):
    """Handles incoming private messages on any connected worker bot."""
    # 1. Schedule 30-minute auto-delete of user's message to keep PM clean
    asyncio.create_task(auto_delete_task(message, 1800))

    main_client = getattr(worker_client, "main_client", worker_client)
    user_id = message.from_user.id if message.from_user else None

    if user_id:
        if not await main_client.mongodb.present_user(user_id):
            try:
                await main_client.mongodb.add_user(user_id)
            except Exception:
                pass
        if await main_client.mongodb.is_banned(user_id):
            return await message.reply(
                "**🚫 You have been banned from using this bot!**"
            )

    text = message.text or ""

    # 2. Check if it's a File Store Deep-Link Payload
    if text.startswith("/start ") and len(text.split(" ", 1)[1].strip()) > 0:
        payload = text.split(" ", 1)[1].strip()
        base64_string = payload
        if base64_string.startswith("yu3elk"):
            base64_string = base64_string[6:-1]

        await resolve_and_deliver_files(
            delivery_client=worker_client,
            main_client=main_client,
            message=message,
            base64_string=base64_string,
            original_payload=payload,
        )
        return

    # 3. Normal /start or generic text message -> Trigger /otherbots custom reply
    config = await main_client.mongodb.get_otherbots_config()
    reply_text = (
        config.get("text")
        or "👋 **Hello! I am a file delivery worker bot.**\nUse a valid file link from our main channel to get your files."
    )
    reply_img = config.get("image")
    btn_name = config.get("button_name")
    btn_url = config.get("button_url")

    markup = None
    if btn_name and btn_url:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(btn_name, url=btn_url)]])

    try:
        if reply_img:
            bot_reply = await message.reply_photo(
                photo=reply_img, caption=reply_text, reply_markup=markup
            )
        else:
            bot_reply = await message.reply_text(
                text=reply_text, reply_markup=markup, disable_web_page_preview=True
            )
        asyncio.create_task(auto_delete_task(bot_reply, 1800))
    except Exception as e:
        main_client.LOGGER(__name__, main_client.name).warning(
            f"Failed to send worker bot reply: {e}"
        )


async def start_single_worker_bot(main_client: Client, token: str) -> dict:
    """Starts a single worker bot in memory and registers its message handler."""
    bot_id_prefix = token.split(":")[0]
    session_name = f"worker_{bot_id_prefix}"

    worker = Client(
        name=session_name,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=token,
        in_memory=True,
    )
    worker.main_client = main_client
    worker.mongodb = main_client.mongodb
    worker.add_handler(MessageHandler(worker_bot_message_handler, filters.private))

    await worker.start()
    me = await worker.get_me()
    worker.username = me.username

    if not hasattr(main_client, "worker_clients"):
        main_client.worker_clients = {}
    main_client.worker_clients[me.id] = worker

    return {
        "bot_id": me.id,
        "username": me.username,
        "name": me.first_name or f"Bot {me.id}",
        "token": token,
    }


async def start_all_worker_bots(main_client: Client):
    """Loads and starts all saved worker bots from MongoDB on main bot startup."""
    if not hasattr(main_client, "worker_clients"):
        main_client.worker_clients = {}

    saved_bots = await main_client.mongodb.get_worker_bots()
    for bot_doc in saved_bots:
        token = bot_doc.get("token")
        bot_id = bot_doc.get("bot_id")
        if not token or bot_id in main_client.worker_clients:
            continue
        try:
            info = await start_single_worker_bot(main_client, token)
            main_client.LOGGER(__name__, main_client.name).info(
                f"Started Worker Bot: @{info['username']} ({info['bot_id']})"
            )
        except Exception as e:
            main_client.LOGGER(__name__, main_client.name).warning(
                f"Failed to start worker bot {bot_id}: {e}"
            )


async def pick_worker_bot(main_client: Client) -> Client:
    """Selects an active worker bot (round-robin if auto_rotate_enabled is ON, else first active)."""
    global _worker_rr_index
    workers = list(getattr(main_client, "worker_clients", {}).values())
    if not workers:
        return None

    ar_enabled = await main_client.mongodb.get_setting("auto_rotate_enabled", False)
    if ar_enabled and len(workers) > 1:
        chosen = workers[_worker_rr_index % len(workers)]
        _worker_rr_index += 1
        return chosen
    return workers[0]


# ================================================================================= #
#                             /bots - MULTI-BOT PANEL                               #
# ================================================================================= #


async def get_multi_bot_panel_data(client: Client):
    mb_status = await client.mongodb.get_setting("multi_bot_enabled", False)
    ar_status = await client.mongodb.get_setting("auto_rotate_enabled", False)
    worker_bots = await client.mongodb.get_worker_bots()
    running_count = len(getattr(client, "worker_clients", {}))

    text = (
        "<blockquote>✦ ᴍᴜʟᴛɪ-ʙᴏᴛ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ ᴘᴀɴᴇʟ</blockquote>\n"
        f"›› **ᴍᴜʟᴛɪ-ʙᴏᴛ sᴛᴀᴛᴜs:** `{'🟢 ON' if mb_status else '🔴 OFF'}`\n"
        f"›› **ᴀᴜᴛᴏ-ʀᴏᴛᴀᴛᴇ ʙᴏᴛs:** `{'🟢 ON' if ar_status else '🔴 OFF'}`\n"
        f"›› **ᴄᴏɴɴᴇᴄᴛᴇᴅ ʙᴏᴛs:** `{running_count} / {len(worker_bots)}` ᴀᴄᴛɪᴠᴇ\n\n"
        "__Configure your delivery worker bots below. Make sure each worker bot is an Admin in your DB Channel(s)!__"
    )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Multi Bot: {'🟢 On' if mb_status else '🔴 Off'}",
                    callback_data="toggle_mb_status",
                ),
                InlineKeyboardButton(
                    f"Auto Rotate: {'🟢 On' if ar_status else '🔴 Off'}",
                    callback_data="toggle_ar_status",
                ),
            ],
            [
                InlineKeyboardButton("➕ Add Bot", callback_data="mb_add_bot"),
                InlineKeyboardButton("➖ Rem Bot", callback_data="mb_rem_bot"),
            ],
            [
                InlineKeyboardButton(
                    f"📋 Bot List ({len(worker_bots)})", callback_data="mb_list_bots"
                )
            ],
            [InlineKeyboardButton("‹ ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="settings")],
        ]
    )
    return text, markup


@Client.on_message(filters.command("bots") & filters.private)
async def multi_bot_panel(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply("❌ You are not authorized to use this command.")

    text, markup = await get_multi_bot_panel_data(client)
    await message.reply(text, reply_markup=markup)


@Client.on_callback_query(filters.regex("^(bots_menu|back_to_bots_panel)$"))
async def bots_menu_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    text, markup = await get_multi_bot_panel_data(client)
    await query.message.edit_text(text, reply_markup=markup)


@Client.on_callback_query(filters.regex("^(toggle_mb_status|toggle_ar_status)$"))
async def toggle_bot_settings(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    if query.data == "toggle_mb_status":
        current = await client.mongodb.get_setting("multi_bot_enabled", False)
        await client.mongodb.set_setting("multi_bot_enabled", not current)
        await query.answer(f"Multi-Bot is now {'ON 🟢' if not current else 'OFF 🔴'}")
    else:
        current = await client.mongodb.get_setting("auto_rotate_enabled", False)
        await client.mongodb.set_setting("auto_rotate_enabled", not current)
        await query.answer(f"Auto-Rotate is now {'ON 🟢' if not current else 'OFF 🔴'}")

    await bots_menu_cb(client, query)


@Client.on_callback_query(filters.regex("^mb_add_bot$"))
async def mb_add_bot_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    await query.answer()
    await query.message.edit_text(
        "<blockquote>**➕ ᴀᴅᴅ ᴡᴏʀᴋᴇʀ ʙᴏᴛ**</blockquote>\n\n"
        "Send the **Bot Token** from @BotFather in the next 60 seconds.\n\n"
        "⚠️ **Important:** Make sure the worker bot is already added as an **Admin** in your Primary DB Channel!",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", callback_data="bots_menu")]]
        ),
    )

    try:
        res = await client.listen(
            user_id=query.from_user.id, filters=filters.text, timeout=60
        )
        token = res.text.strip()
        if ":" not in token:
            return await query.message.edit_text(
                "**❌ Invalid Bot Token format!**",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="bots_menu")]]
                ),
            )

        wait_msg = await res.reply("⏳ **Connecting and verifying worker bot...**")
        try:
            info = await start_single_worker_bot(client, token)
            await client.mongodb.add_worker_bot(
                bot_id=info["bot_id"],
                token=token,
                username=info["username"],
                name=info["name"],
            )
            await wait_msg.edit_text(
                f"✅ **Worker Bot Connected Successfully!**\n\n"
                f"›› **Name:** `{info['name']}`\n"
                f"›› **Username:** @{info['username']}\n"
                f"›› **Bot ID:** `{info['bot_id']}`",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "◂ ʙᴀᴄᴋ ᴛᴏ ʙᴏᴛs", callback_data="bots_menu"
                            )
                        ]
                    ]
                ),
            )
        except Exception as e:
            await wait_msg.edit_text(
                f"❌ **Failed to start worker bot:**\n`{e}`",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="bots_menu")]]
                ),
            )
    except ListenerTimeout:
        await query.message.edit_text(
            "**⏰ Timeout! Please try again.**",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="bots_menu")]]
            ),
        )


@Client.on_callback_query(filters.regex("^mb_rem_bot$"))
async def mb_rem_bot_prompt(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    worker_bots = await client.mongodb.get_worker_bots()
    if not worker_bots:
        return await query.answer("No worker bots connected!", show_alert=True)

    await list_worker_bots(client, query)


@Client.on_callback_query(filters.regex("^mb_list_bots$"))
async def list_worker_bots(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    worker_bots = await client.mongodb.get_worker_bots()
    if not worker_bots:
        return await query.answer("No worker bots connected yet!", show_alert=True)

    buttons = []
    for bot in worker_bots:
        bot_id = bot["bot_id"]
        bot_uname = bot.get("username") or f"Bot {bot_id}"
        url = f"https://t.me/{bot_uname}" if bot.get("username") else "https://t.me"
        buttons.append(
            [
                InlineKeyboardButton(f"🤖 @{bot_uname}", url=url),
                InlineKeyboardButton(
                    "🗑️ Remove bot", callback_data=f"mb_rem_specific_{bot_id}"
                ),
            ]
        )

    buttons.append(
        [InlineKeyboardButton("⚠️ Remove All Bots", callback_data="mb_rem_all")]
    )
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="bots_menu")])

    await query.message.edit_text(
        f"📋 **Connected Worker Bots ({len(worker_bots)}):**\n\nClick **Remove bot** next to any bot to disconnect and delete it:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@Client.on_callback_query(filters.regex("^mb_rem_specific_(\\d+)$"))
async def mb_rem_specific_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    bot_id = int(query.data.split("mb_rem_specific_")[1])

    worker = getattr(client, "worker_clients", {}).pop(bot_id, None)
    if worker:
        try:
            await worker.stop()
        except Exception:
            pass

    await client.mongodb.remove_worker_bot(bot_id)
    await query.answer("Worker bot removed! ✅")

    remaining = await client.mongodb.get_worker_bots()
    if remaining:
        await list_worker_bots(client, query)
    else:
        await bots_menu_cb(client, query)


@Client.on_callback_query(filters.regex("^mb_rem_all$"))
async def mb_rem_all_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    for bot_id, worker in list(getattr(client, "worker_clients", {}).items()):
        try:
            await worker.stop()
        except Exception:
            pass
    if hasattr(client, "worker_clients"):
        client.worker_clients.clear()

    await client.mongodb.clear_all_worker_bots()
    await query.answer("All worker bots removed! ✅", show_alert=True)
    await bots_menu_cb(client, query)


# ================================================================================= #
#                         /otherbots - UNIVERSAL REPLY PANEL                        #
# ================================================================================= #


async def get_otherbots_panel_data(client: Client):
    config = await client.mongodb.get_otherbots_config()
    current_text = config.get("text") or "None"
    current_img = "✅ Set" if config.get("image") else "❌ None"
    current_btn = (
        f"{config.get('button_name')} ({config.get('button_url')})"
        if config.get("button_name")
        else "❌ None"
    )

    text = (
        "<blockquote>⚙️ ᴡᴏʀᴋᴇʀ ʙᴏᴛs ᴜɴɪᴠᴇʀsᴀʟ ʀᴇᴘʟʏ sᴇᴛᴛɪɴɢs</blockquote>\n"
        f"›› **ᴛᴇxᴛ:** `{current_text[:60]}`\n"
        f"›› **ɪᴍᴀɢᴇ:** `{current_img}`\n"
        f"›› **ʙᴜᴛᴛᴏɴ:** `{current_btn}`\n\n"
        "__Changes apply to all connected worker bots immediately.__"
    )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Set Text", callback_data="ob_set_txt"),
                InlineKeyboardButton("Remove Text", callback_data="ob_rem_txt"),
            ],
            [
                InlineKeyboardButton("Set Image", callback_data="ob_set_img"),
                InlineKeyboardButton("Remove Image", callback_data="ob_rem_img"),
            ],
            [
                InlineKeyboardButton("Set Button", callback_data="ob_set_btn"),
                InlineKeyboardButton("Remove Button", callback_data="ob_rem_btn"),
            ],
            [
                InlineKeyboardButton("‹ ʙᴀᴄᴋ", callback_data="settings_page_2"),
                InlineKeyboardButton("✖ ᴄʟᴏsᴇ", callback_data="close"),
            ],
        ]
    )
    return text, markup


@Client.on_message(filters.command("otherbots") & filters.private)
async def other_bots_panel(client: Client, message: Message):
    if not is_admin(client, message.from_user.id):
        return await message.reply("❌ You are not authorized.")

    text, markup = await get_otherbots_panel_data(client)
    await message.reply(text, reply_markup=markup)


@Client.on_callback_query(filters.regex("^otherbots_menu$"))
async def otherbots_menu_cb(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    text, markup = await get_otherbots_panel_data(client)
    await query.message.edit_text(text, reply_markup=markup)


@Client.on_callback_query(filters.regex(r"^ob_(set|rem)_(txt|img|btn)$"))
async def handle_otherbots_settings(client: Client, query: CallbackQuery):
    if not is_admin(client, query.from_user.id):
        return await query.answer("✗ ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ᴜsᴇ ᴛʜɪs!", show_alert=True)

    action, target = query.data.split("_")[1], query.data.split("_")[2]

    if action == "rem":
        if target == "txt":
            await client.mongodb.update_otherbots_config({"text": None})
        elif target == "img":
            await client.mongodb.update_otherbots_config({"image": None})
        elif target == "btn":
            await client.mongodb.update_otherbots_config(
                {"button_name": None, "button_url": None}
            )

        await query.answer("Removed successfully! ✅")
        return await otherbots_menu_cb(client, query)

    await query.answer()
    prompt_msg = "Send the new value within 60 seconds:"
    if target == "btn":
        prompt_msg = (
            "Send Button Name and URL separated by `|` within 60 seconds:\n\n"
            "**Example:** `Main Channel | https://t.me/yourchannel`"
        )
    elif target == "img":
        prompt_msg = "Send a direct image URL (`https://...`) or upload a Photo within 60 seconds:"

    await query.message.edit_text(
        prompt_msg,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◂ ᴄᴀɴᴄᴇʟ", callback_data="otherbots_menu")]]
        ),
    )

    try:
        if target == "txt":
            res = await client.listen(
                user_id=query.from_user.id, filters=filters.text, timeout=60
            )
            await client.mongodb.update_otherbots_config({"text": res.text})

        elif target == "img":
            res = await client.listen(
                user_id=query.from_user.id,
                filters=(filters.photo | filters.text),
                timeout=60,
            )
            if res.photo:
                await client.mongodb.update_otherbots_config(
                    {"image": res.photo.file_id}
                )
            elif res.text and res.text.startswith(("http://", "https://")):
                await client.mongodb.update_otherbots_config(
                    {"image": res.text.strip()}
                )
            else:
                return await query.message.edit_text(
                    "**❌ Invalid image or link!**",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "◂ ʙᴀᴄᴋ", callback_data="otherbots_menu"
                                )
                            ]
                        ]
                    ),
                )

        elif target == "btn":
            res = await client.listen(
                user_id=query.from_user.id, filters=filters.text, timeout=60
            )
            if "|" in res.text:
                name, url = res.text.split("|", 1)
                await client.mongodb.update_otherbots_config(
                    {"button_name": name.strip(), "button_url": url.strip()}
                )
            else:
                return await query.message.edit_text(
                    "**❌ Invalid format! Must include `|` separator.**",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "◂ ʙᴀᴄᴋ", callback_data="otherbots_menu"
                                )
                            ]
                        ]
                    ),
                )

        await otherbots_menu_cb(client, query)
    except ListenerTimeout:
        await query.message.edit_text(
            "**⏰ Timeout! Try again.**",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◂ ʙᴀᴄᴋ", callback_data="otherbots_menu")]]
            ),
        )
