import asyncio
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL = os.getenv("CHANNEL_USERNAME", "").strip()  # e.g. @my_channel
DB_PATH = os.getenv("DB_PATH", "requests.db")

if not BOT_TOKEN or not ADMIN_ID:
    raise RuntimeError("Set BOT_TOKEN and ADMIN_ID in .env")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.execute("""
CREATE TABLE IF NOT EXISTS requests (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 username TEXT,
 demo_number TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued',
 demo_code TEXT,
 created_at TEXT NOT NULL
)
""")
conn.commit()

class Form(StatesGroup):
    demo_number = State()
    demo_code = State()

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Сдать тестовую заявку")],
            [KeyboardButton(text="📋 Мои заявки"), KeyboardButton(text="💬 Поддержка")],
        ],
        resize_keyboard=True
    )

def admin_request_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept:{req_id}"),
         InlineKeyboardButton(text="❌ Отказать", callback_data=f"reject:{req_id}")],
        [InlineKeyboardButton(text="💬 Написать клиенту", callback_data=f"contact:{req_id}")],
    ])

def admin_active_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Запросить тестовый код", callback_data=f"code:{req_id}")],
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done:{req_id}"),
         InlineKeyboardButton(text="❌ Отказать", callback_data=f"fail:{req_id}")],
        [InlineKeyboardButton(text="💬 Написать клиенту", callback_data=f"contact:{req_id}")],
    ])

def admin_done_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Отметить выплату вручную", callback_data=f"paid:{req_id}")]
    ])

def get_request(req_id):
    return conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def subscription_ok(user_id: int) -> bool:
    if not CHANNEL:
        return True
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return member.status in {"creator", "administrator", "member"}
    except Exception:
        return False

async def subscription_prompt(message: Message):
    channel_url = "https://t.me/" + CHANNEL.lstrip("@")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Открыть канал", url=channel_url)],
        [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub")]
    ])
    await message.answer("Чтобы пользоваться ботом, подпишитесь на канал и нажмите «Проверить подписку».", reply_markup=kb)

async def notify_admin(req_id: int):
    r = get_request(req_id)
    if not r:
        return
    user_label = f"@{r['username']}" if r["username"] else "username не задан"
    await bot.send_message(
        ADMIN_ID,
        f"🆕 <b>Новая тестовая заявка #{r['id']}</b>\n"
        f"Пользователь: {user_label}\n"
        f"Telegram ID: <code>{r['user_id']}</code>\n"
        f"Демо-номер: <code>{r['demo_number']}</code>\n"
        f"Статус: очередь",
        reply_markup=admin_request_kb(req_id)
    )

@dp.message(CommandStart())
async def start(message: Message):
    if not await subscription_ok(message.from_user.id):
        await subscription_prompt(message)
        return
    await message.answer(
        "Добро пожаловать в <b>MTS ID Fun</b> — демонстрацию очереди заявок.\n\n"
        "⚠️ Это тестовый бот: вводите только номер формата <code>DEMO-123456</code>. "
        "Не отправляйте реальные номера телефонов, SMS-коды или данные для входа.",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery):
    if await subscription_ok(call.from_user.id):
        await call.message.answer("✅ Подписка подтверждена.", reply_markup=main_menu())
    else:
        await call.answer("Подписка пока не найдена. Подпишитесь на канал.", show_alert=True)
    await call.answer()

@dp.message(F.text == "📱 Сдать тестовую заявку")
async def new_request(message: Message, state: FSMContext):
    if not await subscription_ok(message.from_user.id):
        await subscription_prompt(message)
        return
    await state.set_state(Form.demo_number)
    await message.answer(
        "Введите тестовый идентификатор в формате <code>DEMO-123456</code>.\n"
        "Не вводите настоящий номер телефона.",
    )

@dp.message(Form.demo_number)
async def save_request(message: Message, state: FSMContext):
    value = (message.text or "").strip().upper()
    if not re.fullmatch(r"DEMO-\d{6}", value):
        await message.answer("Неверный формат. Используйте только <code>DEMO-123456</code>.")
        return
    cur = conn.execute(
        "INSERT INTO requests(user_id, username, demo_number, status, created_at) VALUES(?,?,?,?,?)",
        (message.from_user.id, message.from_user.username, value, "queued", datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    req_id = cur.lastrowid
    position = conn.execute(
        "SELECT COUNT(*) AS n FROM requests WHERE status='queued' AND id<=?", (req_id,)
    ).fetchone()["n"]
    await state.clear()
    await message.answer(f"⏳ Вы в очереди <b>#{position}</b>.\nНомер тестовой заявки: <code>#{req_id}</code>")
    await notify_admin(req_id)

@dp.message(F.text == "📋 Мои заявки")
async def my_requests(message: Message):
    rows = conn.execute(
        "SELECT id, demo_number, status FROM requests WHERE user_id=? ORDER BY id DESC LIMIT 10",
        (message.from_user.id,)
    ).fetchall()
    if not rows:
        await message.answer("У вас пока нет заявок.")
        return
    names = {
        "queued": "⏳ В очереди", "accepted": "✅ Принята",
        "code_requested": "🔢 Ожидается тестовый код", "verified": "🟡 Код введён, ожидает решения",
        "done": "✅ Выполнено", "rejected": "❌ Отказано", "paid": "💸 Выплата отмечена вручную",
    }
    await message.answer("\n".join(
        f"Заявка <b>#{r['id']}</b> · <code>{r['demo_number']}</code>\n{names.get(r['status'], r['status'])}"
        for r in rows
    ))

@dp.message(F.text == "💬 Поддержка")
async def support(message: Message):
    await message.answer("Напишите сюда свой вопрос одним сообщением. Владелец бота сможет ответить через уведомление администратора.")
    await message.answer("Чтобы связаться с владельцем, укажите его контакт в настройках проекта или сообщении /start.")

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM requests GROUP BY status").fetchall()
    stats = "\n".join(f"• {r['status']}: {r['n']}" for r in rows) or "Заявок пока нет."
    await message.answer(f"⚙️ <b>Панель владельца</b>\n\n{stats}\n\nНовые заявки будут приходить сюда автоматически.")

@dp.callback_query(F.data.startswith(("accept:", "reject:", "code:", "done:", "fail:", "paid:", "contact:")))
async def admin_actions(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    action, raw_id = call.data.split(":", 1)
    req_id = int(raw_id)
    r = get_request(req_id)
    if not r:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    if action == "accept":
        if r["status"] != "queued":
            await call.answer("Заявка уже обработана.", show_alert=True)
            return
        conn.execute("UPDATE requests SET status='accepted' WHERE id=?", (req_id,))
        conn.commit()
        await bot.send_message(r["user_id"], f"✅ Ваша тестовая заявка <b>#{req_id}</b> принята. Ожидайте дальнейших действий.")
        await call.message.edit_reply_markup(reply_markup=admin_active_kb(req_id))
        await call.answer("Заявка принята.")
    elif action == "reject":
        conn.execute("UPDATE requests SET status='rejected' WHERE id=?", (req_id,))
        conn.commit()
        await bot.send_message(r["user_id"], f"❌ Ваша тестовая заявка <b>#{req_id}</b> отклонена.")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Заявка отклонена.")
    elif action == "code":
        if r["status"] not in ("accepted", "code_requested"):
            await call.answer("Сначала примите заявку.", show_alert=True)
            return
        code = f"{secrets.randbelow(900000) + 100000}"
        conn.execute("UPDATE requests SET status='code_requested', demo_code=? WHERE id=?", (code, req_id))
        conn.commit()
        await bot.send_message(
            r["user_id"],
            f"🔢 Тестовый код для заявки <b>#{req_id}</b>: <code>{code}</code>\n"
            "Это код, созданный самим ботом для демонстрации. Нажмите кнопку ниже и введите его.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⌨️ Ввести тестовый код", callback_data=f"enter_code:{req_id}")]
            ])
        )
        await call.answer("Тестовый код отправлен клиенту.")
    elif action == "done":
        if r["status"] != "verified":
            await call.answer("Сначала клиент должен ввести корректный тестовый код.", show_alert=True)
            return
        conn.execute("UPDATE requests SET status='done' WHERE id=?", (req_id,))
        conn.commit()
        user_label = f"@{r['username']}" if r["username"] else "username не задан"
        await bot.send_message(r["user_id"], f"✅ Заявка <b>#{req_id}</b> выполнена.\nЕсли предусмотрена выплата, свяжитесь с владельцем.")
        await call.message.answer(f"Заявка #{req_id} выполнена.\nКлиент: {user_label}\nTelegram ID: <code>{r['user_id']}</code>", reply_markup=admin_done_kb(req_id))
        await call.answer("Отмечено как выполнено.")
    elif action == "fail":
        conn.execute("UPDATE requests SET status='rejected' WHERE id=?", (req_id,))
        conn.commit()
        await bot.send_message(r["user_id"], f"❌ По заявке <b>#{req_id}</b> принято решение об отказе.")
        await call.answer("Отказ отмечен.")
    elif action == "paid":
        if r["status"] != "done":
            await call.answer("Сначала отметьте заявку выполненной.", show_alert=True)
            return
        conn.execute("UPDATE requests SET status='paid' WHERE id=?", (req_id,))
        conn.commit()
        await bot.send_message(r["user_id"], f"💸 По заявке <b>#{req_id}</b> владелец отметил выплату вручную.")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Выплата отмечена вручную.")
    elif action == "contact":
        await state.update_data(contact_req_id=req_id)
        await call.message.answer(f"Напишите ответ клиенту по заявке #{req_id} следующим сообщением.")
        await state.set_state(ContactState.reply)
        await call.answer()

class ContactState(StatesGroup):
    reply = State()

@dp.callback_query(F.data.startswith("enter_code:"))
async def enter_code(call: CallbackQuery, state: FSMContext):
    req_id = int(call.data.split(":", 1)[1])
    r = get_request(req_id)
    if not r or r["user_id"] != call.from_user.id or r["status"] != "code_requested":
        await call.answer("Эта заявка сейчас не ожидает код.", show_alert=True)
        return
    await state.set_state(Form.demo_code)
    await state.update_data(req_id=req_id)
    await call.message.answer("Введите тестовый код, который выдал этот бот.")
    await call.answer()

@dp.message(Form.demo_code)
async def check_demo_code(message: Message, state: FSMContext):
    data = await state.get_data()
    req_id = data.get("req_id")
    r = get_request(req_id) if req_id else None
    if not r or r["user_id"] != message.from_user.id or r["status"] != "code_requested":
        await state.clear()
        await message.answer("Заявка не найдена или уже обработана.")
        return
    if (message.text or "").strip() != r["demo_code"]:
        await message.answer("❌ Код не совпал. Проверьте тестовый код из сообщения бота и попробуйте ещё раз.")
        return
    conn.execute("UPDATE requests SET status='verified' WHERE id=?", (req_id,))
    conn.commit()
    await state.clear()
    await message.answer("✅ Тестовый код подтверждён. Ожидайте решения администратора.")
    await bot.send_message(ADMIN_ID, f"🟡 Клиент ввёл правильный тестовый код по заявке <b>#{req_id}</b>.", reply_markup=admin_active_kb(req_id))

@dp.message(ContactState.reply)
async def contact_reply(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    req_id = data.get("contact_req_id")
    r = get_request(req_id) if req_id else None
    if not r:
        await state.clear()
        await message.answer("Заявка не найдена.")
        return
    await bot.send_message(r["user_id"], f"💬 Сообщение администратора по заявке #{req_id}:\n\n{message.text or '(пустое сообщение)'}")
    await state.clear()
    await message.answer("Сообщение отправлено клиенту.")

@dp.message()
async def fallback(message: Message):
    if not await subscription_ok(message.from_user.id):
        await subscription_prompt(message)
        return
    await message.answer("Выберите действие в меню ниже.", reply_markup=main_menu())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
