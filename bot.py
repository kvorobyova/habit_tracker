import asyncio
import json
import logging
from datetime import datetime, time as dtime, timedelta
from typing import Set, Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, CommandObject
from aiogram.client.default import DefaultBotProperties

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncpg
from dotenv import load_dotenv
import os

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DB_URL")

# Инициализация бота и планировщика
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Константы для дней недели
WEEK_DAYS = {
    'mon': 'Понедельник',
    'tue': 'Вторник',
    'wed': 'Среда',
    'thu': 'Четверг',
    'fri': 'Пятница',
    'sat': 'Суббота',
    'sun': 'Воскресенье',
    'everyday': 'Каждый день'
}

REMINDER_OPTIONS = {
    '1h': 'За 1 час',
    '3h': 'За 3 часа',
    '1d': 'За 1 день',
    'none': 'Без предупреждения'
}

# Состояния FSM
class AddHabit(StatesGroup):
    name = State()
    time = State()
    days = State()
    reminders = State()

class EditHabit(StatesGroup):
    select_habit = State()
    name = State()
    time = State()
    days = State()
    reminders = State()

# --- Вспомогательные функции ---
async def init_db(pool):
    """Инициализация таблиц в базе данных"""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            habit_id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            time TEXT NOT NULL,
            days JSONB NOT NULL,
            reminders JSONB
        )
    """)
    
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS habit_completions (
            habit_id INTEGER REFERENCES habits(habit_id) ON DELETE CASCADE,
            date DATE NOT NULL,
            completed BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (habit_id, date)
        )
    """)
    
    # Даем права пользователю bot_user
    await pool.execute("GRANT ALL PRIVILEGES ON TABLE habits, habit_completions TO bot_user")
    await pool.execute("GRANT USAGE, SELECT ON SEQUENCE habits_habit_id_seq TO bot_user")

async def get_db_pool():
    pool = await asyncpg.create_pool(DB_URL)
    await init_db(pool)  # Инициализируем таблицы при создании пула
    return pool

# ... (остальные функции остаются без изменений, как в вашем исходном коде) ...

# --- Запуск бота ---
async def on_startup():
    logging.info("Бот запущен")
    # Инициализация базы данных при старте
    pool = await get_db_pool()
    await init_db(pool)
    await pool.close()

async def on_shutdown():
    logging.info("Бот остановлен")

async def main():
    pool = await get_db_pool()
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    scheduler.start()
    await dp.start_polling(bot, pool=pool)

if __name__ == "__main__":
    asyncio.run(main())