import asyncio
import logging
import re
import os
from typing import Dict, List, Set

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.session.aiohttp import AiohttpSession

from google import genai
from google.genai import types as genai_types

# ================= 1. SOZLAMALAR =================
BOT_TOKEN = "8518398052:AAGlwmWHUSrOimynQSWMcr3jRDa97Ca0c3Q"
GEMINI_API_KEY = "AQ.Ab8RN6KEgEnJm-ru_lwL3zkZqggH7ZEzLsKKDFFls2dQP-tBOg"
ADMIN_PASSWORD = "1234"

REQUIRED_CHANNELS = ["@anorairobot"]

BAD_WORDS = [
    "ahmoq", "tentak", "soliq", "xrom", "jalap", "sik", "am", "qotoq",
    "yebsan", "dalbayob", "pizdez", "suka", "boshog'riq", "tupoy"
]

DYNAMIC_PROMPTS = {
    "polite": "Siz juda hushmuomala, mehmondost va izzat-ikromli AI yordamchisiz.",
    "angry": "Siz juda jahldor, tajang sun'iy intellektsiz. Qisqa va tezroq javob bering.",
    "funny": "Siz juda hazilkash va quvnoqsiz. Har bir javobga kulgili hazil qo'shing.",
    "business": "Siz rasmiy va professional biznes konsul'tantsiz. Faqat faktlarga tayaning.",
    "pirate": "Siz qaroqchisiz! 'Arr!', 'Kema kapitani!' iboralarini ishlatib gapiring."
}
CURRENT_SYSTEM_PROMPT = DYNAMIC_PROMPTS["polite"]

USER_DATA: Dict[int, dict] = {}
USER_CONTEXT: Dict[int, List[genai_types.Content]] = {}
USER_HISTORY: Dict[int, List[str]] = {}
USER_WARNINGS: Dict[int, int] = {}
BANNED_USERS: Set[int] = set()
ADMIN_USERS: Set[int] = set()

class AdminStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_broadcast = State()
    waiting_for_channel_post = State()
    waiting_for_ban_id = State()
    waiting_for_unban_id = State()
    waiting_for_custom_prompt = State()
    waiting_for_view_chat_id = State()

# ================= 2. INIZIALIZATSIYA =================
logging.basicConfig(level=logging.INFO)

# PythonAnywhere proksi sozlamalari
os.environ["HTTP_PROXY"] = "http://proxy.server:3128"
os.environ["HTTPS_PROXY"] = "http://proxy.server:3128"

session = AiohttpSession(proxy="http://proxy.server:3128")
bot = Bot(token=BOT_TOKEN, session=session)

dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# ================= 3. YORDAMCHI FUNKSIYALAR =================
def register_user(user: types.User):
    USER_DATA[user.id] = {
        "name": user.first_name or "Foydalanuvchi",
        "username": user.username or "Yo'q"
    }
    if user.id not in USER_HISTORY:
        USER_HISTORY[user.id] = []
    if user.id not in USER_WARNINGS:
        USER_WARNINGS[user.id] = 0

def check_bad_words(text: str) -> bool:
    text_lower = text.lower()
    for word in BAD_WORDS:
        if re.search(r'\b' + re.escape(word) + r'\b', text_lower):
            return True
    return False

async def check_subscription(user_id: int, bot: Bot) -> bool:
    if not REQUIRED_CHANNELS:
        return True
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            return False
    return True

def get_sub_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for channel in REQUIRED_CHANNELS:
        url = f"https://t.me/{channel.replace('@', '')}"
        buttons.append([InlineKeyboardButton(text=f"📢 {channel} ga obuna bo'lish", url=url)])
    buttons.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Xotirani tozalash", callback_data="clear_context")]
    ])

def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Foydalanuvchilar Ro'yxati", callback_data="admin_manage_users")],
        [InlineKeyboardButton(text="💬 Chatlar Tarixini Ko'rish", callback_data="admin_view_chat")],
        [InlineKeyboardButton(text="📢 Kanallarga Post Joylash", callback_data="admin_post_channel")],
        [InlineKeyboardButton(text="📨 Userlarga Xabar Yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🎭 AI Rejimlarini O'zgartirish", callback_data="admin_ai_modes")],
        [
            InlineKeyboardButton(text="🚫 Bloklash (Ban)", callback_data="admin_ban"),
            InlineKeyboardButton(text="✅ Blokdan Chiqarish", callback_data="admin_unban")
        ],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🚪 Chiqish", callback_data="admin_logout")]
    ])

def get_ai_modes_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😊 Hushmuomala", callback_data="mode_polite"), InlineKeyboardButton(text="😡 Jahldor", callback_data="mode_angry")],
        [InlineKeyboardButton(text="🤣 Hazilkash", callback_data="mode_funny"), InlineKeyboardButton(text="💼 Biznes", callback_data="mode_business")],
        [InlineKeyboardButton(text="🏴‍☠️ Qaroqchi", callback_data="mode_pirate")],
        [InlineKeyboardButton(text="✍️ Maxsus Prompt", callback_data="mode_custom")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_back")]
    ])

# ================= 4. HANDLERLAR =================

@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    register_user(message.from_user)

    if user_id in BANNED_USERS:
        await message.answer("❌ **Siz botdan foydalanish uchun bloklangansiz!**", parse_mode="Markdown")
        return

    is_subscribed = await check_subscription(user_id, message.bot)
    if not is_subscribed:
        await message.answer(
            "⚠️ **Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:**",
            reply_markup=get_sub_keyboard(),
            parse_mode="Markdown"
        )
        return

    USER_CONTEXT[user_id] = []
    user_name = USER_DATA[user_id]["name"]
    await message.answer(
        f"Salom, **{user_name}**!\n\n🤖 Men **Anorcha AI** sun'iy intellekt yordamchisiman.\nMenga istalgan savolingizni berishingiz mumkin!",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "check_sub")
async def cb_check_sub(call: CallbackQuery):
    is_subscribed = await check_subscription(call.from_user.id, call.bot)
    if is_subscribed:
        await call.message.delete()
        await call.message.answer("✅ Obuna tasdiqlandi! Savolingizni yuborishingiz mumkin.", reply_markup=get_main_keyboard())
    else:
        await call.answer("❌ Siz hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)

@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message, state: FSMContext):
    register_user(message.from_user)
    if message.from_user.id in ADMIN_USERS:
        await message.answer("👑 **Admin Panelga xush kelibsiz!**", reply_markup=get_admin_keyboard(), parse_mode="Markdown")
    else:
        await state.set_state(AdminStates.waiting_for_password)
        await message.answer("🔒 Admin panelga kirish uchun maxfiy kodni kiriting:")

@router.message(AdminStates.waiting_for_password)
async def process_admin_password(message: Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        ADMIN_USERS.add(message.from_user.id)
        await state.clear()
        await message.answer("✅ Parol to'g'ri! **Admin Panel.**", reply_markup=get_admin_keyboard(), parse_mode="Markdown")
    else:
        await message.answer("❌ Noto'g'ri parol!")

@router.callback_query(F.data == "admin_manage_users")
async def cb_manage_users(call: CallbackQuery):
    if call.from_user.id not in ADMIN_USERS: return
    await call.answer()

    msg = "👥 **FOYDALANUVCHILAR RO'YXATI:**\n\n"
    for uid, uinfo in list(USER_DATA.items())[-10:]:
        status = "🚫 (BLOKLANGAN)" if uid in BANNED_USERS else "✅ (FAOL)"
        warns = USER_WARNINGS.get(uid, 0)
        last_msg = USER_HISTORY[uid][-1] if USER_HISTORY.get(uid) else "Hali yozmagan"
        msg += f"• **{uinfo['name']}** (`ID: {uid}`) [{status}]\n  └ ⚠️ Ogohlantirishlar: {warns}/5\n  └ 💬 Oxirgi xabar: _{last_msg}_\n\n"

    if not USER_DATA:
        msg += "Hali foydalanuvchilar yo'q."

    await call.message.answer(msg, reply_markup=get_admin_keyboard(), parse_mode="Markdown")

@router.callback_query(F.data == "admin_view_chat")
async def cb_view_chat(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_USERS: return
    await call.answer()
    await state.set_state(AdminStates.waiting_for_view_chat_id)
    await call.message.answer("🔎 **Xabarlar tarixini ko'rmoqchi bo'lgan foydalanuvchi ID sini yuboring:**")

@router.message(AdminStates.waiting_for_view_chat_id)
async def process_view_chat_id(message: Message, state: FSMContext):
    await state.clear()
    if message.text.isdigit():
        target_id = int(message.text)
        if target_id in USER_HISTORY and USER_HISTORY[target_id]:
            history_text = f"💬 **USER (`{target_id}`) CHAT TARIXI:**\n\n"
            for idx, msg in enumerate(USER_HISTORY[target_id][-15:], 1):
                history_text += f"{idx}. {msg}\n"
            await message.answer(history_text, reply_markup=get_admin_keyboard(), parse_mode="Markdown")
        else:
            await message.answer("⚠️ Bu foydalanuvchidan hech qanday xabar kelmagan yoki ID noto'g'ri.", reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ ID faqat raqamlardan iborat bo'lishi kerak!")

@router.callback_query(F.data == "admin_post_channel")
async def cb_post_channel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_USERS: return
    await call.answer()
    await state.set_state(AdminStates.waiting_for_channel_post)
    await call.message.answer("📢 **Kanallarga yuboriladigan post matnini yuboring:**")

@router.message(AdminStates.waiting_for_channel_post)
async def process_channel_post(message: Message, state: FSMContext):
    await state.clear()
    sent_count = 0
    for ch in REQUIRED_CHANNELS:
        try:
            await bot.send_message(chat_id=ch, text=message.text, parse_mode="Markdown")
            sent_count += 1
        except Exception as e:
            logging.error(f"Kanalga post yuborishda xatolik: {e}")

    await message.answer(f"✅ Post **{sent_count} ta** kanalga muvaffaqiyatli joylandi!", reply_markup=get_admin_keyboard())

@router.callback_query(F.data == "admin_ban")
async def cb_admin_ban(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_USERS: return
    await call.answer()
    await state.set_state(AdminStates.waiting_for_ban_id)
    await call.message.answer("🚫 **Bloklash uchun Telegram ID sini yuboring:**")

@router.message(AdminStates.waiting_for_ban_id)
async def process_ban_user(message: Message, state: FSMContext):
    await state.clear()
    if message.text.isdigit():
        target_id = int(message.text)
        BANNED_USERS.add(target_id)
        await message.answer(f"✅ Foydalanuvchi (`{target_id}`) **bloklandi!**", reply_markup=get_admin_keyboard(), parse_mode="Markdown")
    else:
        await message.answer("❌ ID faqat raqamlardan iborat bo'lishi kerak!")

@router.callback_query(F.data == "admin_unban")
async def cb_admin_unban(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_USERS: return
    await call.answer()
    await state.set_state(AdminStates.waiting_for_unban_id)
    await call.message.answer("✅ **Blokdan chiqarish uchun Telegram ID sini yuboring:**")

@router.message(AdminStates.waiting_for_unban_id)
async def process_unban_user(message: Message, state: FSMContext):
    await state.clear()
    if message.text.isdigit():
        target_id = int(message.text)
        BANNED_USERS.discard(target_id)
        USER_WARNINGS[target_id] = 0
        await message.answer(f"✅ Foydalanuvchi (`{target_id}`) **blokdan chiqarildi va ogohlantirishlari tozalandi!**", reply_markup=get_admin_keyboard(), parse_mode="Markdown")
    else:
        await message.answer("❌ ID faqat raqamlardan iborat bo'lishi kerak!")

@router.callback_query(F.data == "admin_ai_modes")
async def cb_admin_ai_modes(call: CallbackQuery):
    if call.from_user.id not in ADMIN_USERS: return
    await call.answer()
    await call.message.edit_text("🎭 **AI Xarakterini Tanlang:**", reply_markup=get_ai_modes_keyboard())

@router.callback_query(F.data.startswith("mode_"))
async def cb_set_mode(call: CallbackQuery, state: FSMContext):
    global CURRENT_SYSTEM_PROMPT
    if call.from_user.id not in ADMIN_USERS: return
    mode = call.data.split("_")[1]

    if mode == "custom":
        await state.set_state(AdminStates.waiting_for_custom_prompt)
        await call.message.answer("✍️ AI uchun maxsus ko'rsatmani yozing:")
        return

    if mode in DYNAMIC_PROMPTS:
        CURRENT_SYSTEM_PROMPT = DYNAMIC_PROMPTS[mode]
        await call.message.edit_text(f"✅ AI rejimi o'zgardi:\n\n_{CURRENT_SYSTEM_PROMPT}_", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

@router.message(AdminStates.waiting_for_custom_prompt)
async def process_custom_prompt(message: Message, state: FSMContext):
    global CURRENT_SYSTEM_PROMPT
    CURRENT_SYSTEM_PROMPT = message.text
    await state.clear()
    await message.answer(f"✅ Maxsus ko'rsatma o'rnatildi:\n\n_{CURRENT_SYSTEM_PROMPT}_", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

@router.callback_query(F.data == "admin_back")
async def cb_admin_back(call: CallbackQuery):
    if call.from_user.id in ADMIN_USERS:
        await call.message.edit_text("👑 **Admin Panel:**", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(call: CallbackQuery):
    if call.from_user.id not in ADMIN_USERS: return
    await call.answer()
    stats = (
        "📊 **TIZIM STATISTIKASI:**\n\n"
        f"👥 Barcha foydalanuvchilar: **{len(USER_DATA)} ta**\n"
        f"🚫 Bloklanganlar: **{len(BANNED_USERS)} ta**\n"
        f"📢 Boshqariladigan Kanallar: **{len(REQUIRED_CHANNELS)} ta**"
    )
    await call.message.answer(stats, reply_markup=get_admin_keyboard(), parse_mode="Markdown")

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_USERS: return
    await call.answer()
    await state.set_state(AdminStates.waiting_for_broadcast)
    await call.message.answer("📢 Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:")

@router.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    await state.clear()
    success = 0
    for uid in USER_DATA.keys():
        if uid not in BANNED_USERS:
            try:
                await bot.send_message(chat_id=uid, text=f"📢 **ADMIN XABARI:**\n\n{message.text}", parse_mode="Markdown")
                success += 1
                await asyncio.sleep(0.05)
            except Exception: pass
    await message.answer(f"✅ Xabar **{success} ta** foydalanuvchiga yuborildi!", reply_markup=get_admin_keyboard())

@router.callback_query(F.data == "admin_logout")
async def cb_admin_logout(call: CallbackQuery):
    if call.from_user.id in ADMIN_USERS:
        ADMIN_USERS.remove(call.from_user.id)
    await call.answer("Admin paneldan chiqdingiz!")
    await call.message.edit_text("🚪 Chiqildi.", reply_markup=get_main_keyboard())

@router.callback_query(F.data == "clear_context")
async def cb_clear_context(call: CallbackQuery):
    USER_CONTEXT[call.from_user.id] = []
    await call.answer("Muloqot tarixi tozalandi!", show_alert=True)

# ================= 5. AI CHAT HANDLERI VA SO'KINISH FILTRI =================
@router.message(F.text, F.chat.type == "private")
async def handle_ai_chat(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id in BANNED_USERS:
        await message.answer("❌ **Siz botdan foydalanish uchun bloklangansiz!**", parse_mode="Markdown")
        return

    if (await state.get_state()) is not None:
        return

    is_subscribed = await check_subscription(user_id, message.bot)
    if not is_subscribed:
        await message.answer("⚠️ **Botdan foydalanish uchun kanallarga obuna bo'ling:**", reply_markup=get_sub_keyboard(), parse_mode="Markdown")
        return

    register_user(message.from_user)

    USER_HISTORY[user_id].append(message.text)

    # Auto-ban mantiqi
    if check_bad_words(message.text):
        USER_WARNINGS[user_id] += 1
        current_warns = USER_WARNINGS[user_id]

        if current_warns >= 5:
            BANNED_USERS.add(user_id)
            await message.reply("🚫 **Siz 5 martadan ortiq nojo'ya/haqoratli so'zlar ishlatganingiz uchun botdan avtomattik BLOKLANDINGIZ!**", parse_mode="Markdown")

            for admin_id in ADMIN_USERS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"⚠️ **AUTO-BAN:** Foydalanuvchi **{message.from_user.first_name}** (`ID: {user_id}`) 5 marta so'kingani uchun avtomatik bloklandi!",
                        parse_mode="Markdown"
                    )
                except Exception: pass
            return
        else:
            await message.reply(
                f"⚠️ **O'zingizni bosib oling!** Botda haqoratli so'zlar ishlatish taqiqlangan.\n"
                f"🔴 **Ogohlantirish:** {current_warns}/5\n"
                f"_(5 ta ogohlantirishdan so'ng avtomatik bloklanasiz!)_",
                parse_mode="Markdown"
            )
            return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    if user_id not in USER_CONTEXT:
        USER_CONTEXT[user_id] = []

    if len(USER_CONTEXT[user_id]) > 10:
        USER_CONTEXT[user_id] = USER_CONTEXT[user_id][-10:]

    USER_CONTEXT[user_id].append(
        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=message.text)])
    )

    try:
        response = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=USER_CONTEXT[user_id],
            config=genai_types.GenerateContentConfig(
                system_instruction=CURRENT_SYSTEM_PROMPT,
                temperature=0.7,
            )
        )

        ai_response_text = response.text
        USER_CONTEXT[user_id].append(
            genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=ai_response_text)])
        )

        await message.reply(ai_response_text, reply_markup=get_main_keyboard())

    except Exception as e:
        logging.error(f"AI Xatosi: {e}")
        await message.reply("⚠️ Xatolik yuz berdi. Bir ozdan so'ng qayta urinib ko'ring yoki xotirani tozalang.")

# ================= 6. BOTNI ISHGA TUSHIRISH =================
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("🚀 Bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot to'xtatildi.")
