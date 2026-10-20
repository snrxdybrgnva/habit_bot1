import asyncio
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite

# .env faylidan maxfiy ma'lumotlarni yuklash
load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- FSM Holatlar ---
class TaskSubmission(StatesGroup):
    waiting_for_photo = State()

class AdminAddTask(StatesGroup):
    waiting_for_task_text = State()

class AdminRejectTask(StatesGroup):
    waiting_for_reason = State()
    target_user_id = State()

# --- DATABASE INIZIALIZATSIYASI ---
async def init_db():
    async with aiosqlite.connect("study_bot.db") as db:
        # Foydalanuvchilar jadvali (Streak va oxirgi topshirgan sanasi bilan)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                coins INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_submission TEXT
            )
        """)
        # Kunlik vazifalar jadvali
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_text TEXT,
                created_at TEXT
            )
        """)
        await db.commit()

# --- START VA RO'YXATDAN O'TISH ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    full_name = message.from_user.full_name

    async with aiosqlite.connect("study_bot.db") as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, full_name, coins, streak) VALUES (?, ?, 0, 0)",
            (user_id, full_name)
        )
        await db.commit()

    kb = [
        [types.KeyboardButton(text="📚 Bugungi vazifalar"), types.KeyboardButton(text="📤 Vazifa topshirish")],
        [types.KeyboardButton(text="👤 Profil va Streak"), types.KeyboardButton(text="🏆 Reyting")],
        [types.KeyboardButton(text="🛒 Coin Do'koni")]
    ]
    if user_id == ADMIN_ID:
        kb.append([types.KeyboardButton(text="➕ Yangi vazifa qo'shish")])

    keyboard = types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    await message.answer(
        f"Xush kelibsiz, **{full_name}**! 👋\n\n"
        "Vazifalarni bajarib, rasmlarni yuboring va **Coin** hamda **🔥 Streak**lar yiging!",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

# --- 1. VAZIFALAR QISMI ---

@dp.message(F.text == "➕ Yangi vazifa qo'shish")
async def add_task_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminAddTask.waiting_for_task_text)
    await message.answer("📝 **Yangi vazifa matnini kiriting:**\n(Masalan:\n1. Calculus 1.8\n2. Programming 1-masala\n3. History konspekt)", parse_mode="Markdown")

@dp.message(AdminAddTask.waiting_for_task_text)
async def add_task_finish(message: types.Message, state: FSMContext):
    task_text = message.text
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    async with aiosqlite.connect("study_bot.db") as db:
        await db.execute("INSERT INTO tasks (task_text, created_at) VALUES (?, ?)", (task_text, today_date))
        await db.commit()
    
    await state.clear()
    await message.answer("✅ **Bugungi vazifalar muvaffaqiyatli saqlandi!**")

@dp.message(F.text == "📚 Bugungi vazifalar")
async def show_tasks(message: types.Message):
    async with aiosqlite.connect("study_bot.db") as db:
        async with db.execute("SELECT task_text, created_at FROM tasks ORDER BY id DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()

    if row:
        await message.answer(f"📌 **Bugungi vazifalar ({row[1]}):**\n\n{row[0]}", parse_mode="Markdown")
    else:
        await message.answer(" Hozircha vazifalar kiritilmagan.")

# --- 2. VAZIFA TOPSHIRISH VA STREAK MANTIQLARI ---

@dp.message(F.text == "📤 Vazifa topshirish")
async def submit_task_start(message: types.Message, state: FSMContext):
    await state.set_state(TaskSubmission.waiting_for_photo)
    await message.answer("📷 Bajarilgan vazifangiz rasmini yuboring (Izoh qo'shishingiz ham mumkin):")

@dp.message(TaskSubmission.waiting_for_photo, F.photo)
async def submit_task_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    photo_id = message.photo[-1].file_id
    caption = message.caption or "Izoh berilmagan"

    await state.clear()
    await message.answer("⏳ **Rasmingiz adminga tekshirish uchun yuborildi.** Kuting!")

    approve_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="➕ 5 Coin", callback_data=f"give_5_{user_id}"),
            types.InlineKeyboardButton(text="➕ 10 Coin", callback_data=f"give_10_{user_id}"),
            types.InlineKeyboardButton(text="➕ 15 Coin", callback_data=f"give_15_{user_id}")
        ],
        [types.InlineKeyboardButton(text="❌ Sabab bilan rad etish", callback_data=f"reject_reason_{user_id}")]
    ])

    await bot.send_photo(
        chat_id=ADMIN_ID,
        photo=photo_id,
        caption=f"📥 **Yangi topshiriq keldi!**\n\n👤 **O'quvchi:** {user_name} (ID: `{user_id}`)\n💬 **Izoh:** {caption}",
        parse_mode="Markdown",
        reply_markup=approve_kb
    )

# --- 3. ADMIN TEKSHIRUVI (COIN BERISH VA STREAK OSHIRISH) ---

@dp.callback_query(F.data.startswith("give_"))
async def process_give_coin(callback: types.CallbackQuery):
    data = callback.data.split("_")
    coins_to_add = int(data[1])
    target_user_id = int(data[2])
    today = datetime.now().strftime("%Y-%m-%d")

    async with aiosqlite.connect("study_bot.db") as db:
        async with db.execute("SELECT streak, last_submission FROM users WHERE user_id = ?", (target_user_id,)) as cursor:
            row = await cursor.fetchone()
            current_streak = row[0] if row else 0
            last_sub = row[1] if row else None

        # Streak mantiqini hisoblash
        new_streak = current_streak
        if last_sub:
            last_date = datetime.strptime(last_sub, "%Y-%m-%d").date()
            yesterday = datetime.now().date() - timedelta(days=1)
            
            if last_date == yesterday:
                new_streak += 1
            elif last_date < yesterday:
                new_streak = 1
        else:
            new_streak = 1

        await db.execute(
            "UPDATE users SET coins = coins + ?, streak = ?, last_submission = ? WHERE user_id = ?",
            (coins_to_add, new_streak, today, target_user_id)
        )
        await db.commit()

    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=f"🎉 **Topshiriq qabul qilindi!**\n\n💰 **+{coins_to_add} Coin** berildi.\n🔥 **Streak:** {new_streak} kun!"
        )
    except Exception:
        pass

    await callback.message.edit_caption(
        caption=callback.message.caption + f"\n\n✅ **Tasdiqlandi! (+{coins_to_add} coin, 🔥 {new_streak} streak)**"
    )
    await callback.answer("Coin berildi!")

# Admin tomonidan rad etish va sabab so'rash
@dp.callback_query(F.data.startswith("reject_reason_"))
async def start_reject_reason(callback: types.CallbackQuery, state: FSMContext):
    target_user_id = int(callback.data.split("_")[2])
    await state.set_state(AdminRejectTask.waiting_for_reason)
    await state.update_data(target_user_id=target_user_id)
    await callback.message.answer(f"❌ ID: `{target_user_id}` bo'lgan o'quvchiga rad etish sababini yozib yuboring:")
    await callback.answer()

@dp.message(AdminRejectTask.waiting_for_reason)
async def send_reject_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    reason = message.text

    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=f"❌ **Vazifangiz rad etildi.**\n\n💬 **Sabab:** {reason}"
        )
        await message.answer("✅ Rad etish sababi o'quvchiga yuborildi.")
    except Exception:
        await message.answer("❌ O'quvchiga xabar yuborishda xatolik bo'ldi.")

    await state.clear()

# --- 4. PROFIL, REYTING VA COIN DO'KONI ---

@dp.message(F.text == "👤 Profil va Streak")
async def show_profile(message: types.Message):
    user_id = message.from_user.id
    async with aiosqlite.connect("study_bot.db") as db:
        async with db.execute("SELECT full_name, coins, streak FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()

    if row:
        name, coins, streak = row
        await message.answer(
            f"👤 **Shaxsiy Profilingiz:**\n\n"
            f"👤 **Ism:** {name}\n"
            f"💰 **Jami Coinlar:** {coins} coin\n"
            f"🔥 **Uzluksiz kunlar (Streak):** {streak} kun",
            parse_mode="Markdown"
        )

@dp.message(F.text == "🏆 Reyting")
async def show_leaderboard(message: types.Message):
    async with aiosqlite.connect("study_bot.db") as db:
        async with db.execute("SELECT full_name, coins, streak FROM users ORDER BY coins DESC LIMIT 10") as cursor:
            top_users = await cursor.fetchall()

    if not top_users:
        await message.answer("Hali hech kimda coin mavjud emas.")
        return

    text = "🏆 **O'quvchilar Reytingi (Top-10):**\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, (name, coins, streak) in enumerate(top_users, 1):
        icon = medals[i-1] if i <= 3 else f"{i}."
        text += f"{icon} **{name}** — 💰 {coins} coin | 🔥 {streak} kun\n"

    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🛒 Coin Do'koni")
async def show_shop(message: types.Message):
    text = (
        "🛒 **Coin Do'koni va Imtiyozlar:**\n\n"
        "🎁 **50 Coin** — 1 ta uy vazifasini kechiktirib topshirish imkoniyati\n"
        "🎁 **100 Coin** — Qo'shimcha o'quv qo'llanmasi yoki kitob\n"
        "🎁 **150 Coin** — Yakuniy imtihonga +5 bonus ball\n\n"
        "ℹ️ *Imtiyozlarni xarid qilish uchun to'plagan coinlaringiz bilan Adminga murojaat qiling!*"
    )
    await message.answer(text, parse_mode="Markdown")

# --- MAIN RUNNER ---
async def main():
    await init_db()
    print("Bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())