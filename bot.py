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
async def get_db_pool():
    return await asyncpg.create_pool(DB_URL)

def get_days_keyboard(selected_days: Set[str] = None) -> InlineKeyboardMarkup:
    if selected_days is None:
        selected_days = set()
    
    buttons = []
    row = []
    
    for code, day in WEEK_DAYS.items():
        if code == 'everyday':
            continue
        
        emoji = "✅" if code in selected_days else ""
        row.append(InlineKeyboardButton(
            text=f"{emoji}{day[:3]}",
            callback_data=f"day_{code}"
        ))
        
        if len(row) == 4:
            buttons.append(row)
            row = []
    
    if row:
        buttons.append(row)
    
    buttons.append([
        InlineKeyboardButton(
            text="✅ Каждый день" if 'everyday' in selected_days else "Каждый день",
            callback_data="day_everyday"
        )
    ])
    
    buttons.append([
        InlineKeyboardButton(text="➡️ Далее", callback_data="days_done")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_reminders_keyboard(selected_reminders: Set[str] = None) -> InlineKeyboardMarkup:
    if selected_reminders is None:
        selected_reminders = set()
    
    buttons = []
    
    for code, text in REMINDER_OPTIONS.items():
        emoji = "✅" if code in selected_reminders else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji}{text}",
                callback_data=f"reminder_{code}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="reminders_back"),
        InlineKeyboardButton(text="✅ Готово", callback_data="reminders_done")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def calculate_reminder_time(day_code: str, habit_time: str) -> datetime:
    days_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    hour, minute = map(int, habit_time.split(":"))
    today = datetime.now().weekday()
    target_day = days_map[day_code]
    delta = (target_day - today) % 7
    reminder_date = datetime.now() + timedelta(days=delta)
    return reminder_date.replace(hour=hour, minute=minute)

# --- Основные команды ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я — твой трекер привычек.\n"
        "Я помогу тебе выработать полезные привычки и не забывать о них.\n\n"
        "Введите команду /help, чтобы увидеть список доступных команд."
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    commands = [
        "/start - Начать работу",
        "/addhabit - Добавить привычку",
        "/myhabits - Мои привычки",
        "/edithabit - Редактировать привычку",
        "/daily - Привычки на сегодня",
        "/stats - Статистика выполнения",
        "/weekly_stats - Статистика за неделю",
        "/backup - Резервная копия данных",
        "/cancel - Отменить текущее действие",
        "/help - Список команд"
    ]
    await message.answer("🛠 <b>Доступные команды:</b>\n" + "\n".join(commands))

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("🔹 Сейчас нет активных действий.")
    else:
        await state.clear()
        await message.answer("❌ Действие отменено.")

# --- Привычки ---
@dp.message(Command("addhabit"))
async def cmd_addhabit(message: Message, state: FSMContext):
    await message.answer("Какую привычку ты хочешь добавить?")
    await state.set_state(AddHabit.name)

@dp.message(AddHabit.name)
async def process_name(message: Message, state: FSMContext, pool: asyncpg.Pool):
    existing = await pool.fetchrow(
        "SELECT 1 FROM habits WHERE user_id = $1 AND name = $2",
        message.from_user.id, message.text
    )
    if existing:
        await message.answer("❌ Привычка с таким названием уже существует. Придумайте другое название:")
        return
    
    await state.update_data(habit_name=message.text)
    await message.answer("Во сколько напоминать? (например, 09:00)")
    await state.set_state(AddHabit.time)

@dp.message(AddHabit.time)
async def process_time(message: Message, state: FSMContext):
    try:
        hour, minute = map(int, message.text.split(':'))
        dtime(hour=hour, minute=minute)
        
        await state.update_data(time=message.text)
        await message.answer(
            "Выберите дни недели:",
            reply_markup=get_days_keyboard()
        )
        await state.set_state(AddHabit.days)
    except ValueError:
        await message.answer("❌ Неверный формат времени. Введите в формате ЧЧ:ММ")

@dp.callback_query(AddHabit.days, F.data.startswith("day_"))
async def process_days(callback: CallbackQuery, state: FSMContext):
    day_code = callback.data.split('_')[1]
    data = await state.get_data()
    
    if 'days' not in data:
        data['days'] = set()
    
    if day_code == 'everyday':
        data['days'] = set(WEEK_DAYS.keys()) - {'everyday'}
    elif day_code in data['days']:
        data['days'].remove(day_code)
    else:
        data['days'].add(day_code)
    
    await state.update_data(days=data['days'])
    await callback.message.edit_reply_markup(reply_markup=get_days_keyboard(data['days']))
    await callback.answer()

@dp.callback_query(AddHabit.days, F.data == "days_done")
async def process_days_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    if not data.get('days'):
        await callback.answer("❌ Нужно выбрать хотя бы один день")
        return
    
    await callback.message.edit_text(
        "Выберите предварительные напоминания:",
        reply_markup=get_reminders_keyboard()
    )
    await state.set_state(AddHabit.reminders)
    await callback.answer()

@dp.callback_query(AddHabit.reminders, F.data.startswith("reminder_"))
async def process_reminders(callback: CallbackQuery, state: FSMContext):
    reminder_code = callback.data.split('_')[1]
    data = await state.get_data()
    
    if 'reminders' not in data:
        data['reminders'] = set()
    
    if reminder_code == 'none':
        data['reminders'] = set()
    elif reminder_code in data['reminders']:
        data['reminders'].remove(reminder_code)
    else:
        data['reminders'].add(reminder_code)
    
    await state.update_data(reminders=data['reminders'])
    await callback.message.edit_reply_markup(reply_markup=get_reminders_keyboard(data['reminders']))
    await callback.answer()

@dp.callback_query(AddHabit.reminders, F.data == "reminders_back")
async def process_reminders_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await callback.message.edit_text(
        "Выберите дни недели:",
        reply_markup=get_days_keyboard(data.get('days', set()))
    )
    await state.set_state(AddHabit.days)
    await callback.answer()

@dp.callback_query(AddHabit.reminders, F.data == "reminders_done")
async def process_reminders_done(callback: CallbackQuery, state: FSMContext, pool: asyncpg.Pool):
    data = await state.get_data()
    habit_name = data['habit_name']
    time_str = data['time']
    days = data.get('days', set())
    reminders = data.get('reminders', set())
    
    try:
        # Сохраняем привычку в БД
        habit_id = await pool.fetchval(
            """INSERT INTO habits (user_id, name, time, days, reminders)
               VALUES ($1, $2, $3, $4, $5) RETURNING habit_id""",
            callback.from_user.id, habit_name, time_str, json.dumps(list(days)), json.dumps(list(reminders))
        )
        
        # Настраиваем напоминания
        hour, minute = map(int, time_str.split(':'))
        
        for day in days:
            if day == 'everyday':
                continue
                
            # Основное напоминание
            scheduler.add_job(
                send_reminder,
                'cron',
                day_of_week=day[:3],
                hour=hour,
                minute=minute,
                args=[callback.message.chat.id, habit_name, habit_id, pool],
                id=f"{habit_id}_{day}_main",
                replace_existing=True
            )
            
            # Предварительные напоминания
            for reminder in reminders:
                if reminder == '1h' and hour > 0:
                    scheduler.add_job(
                        send_reminder,
                        'cron',
                        day_of_week=day[:3],
                        hour=hour-1,
                        minute=minute,
                        args=[callback.message.chat.id, f"Через час: {habit_name}", habit_id, pool],
                        id=f"{habit_id}_{day}_pre1h",
                        replace_existing=True
                    )
                elif reminder == '3h' and hour > 2:
                    scheduler.add_job(
                        send_reminder,
                        'cron',
                        day_of_week=day[:3],
                        hour=hour-3,
                        minute=minute,
                        args=[callback.message.chat.id, f"Через 3 часа: {habit_name}", habit_id, pool],
                        id=f"{habit_id}_{day}_pre3h",
                        replace_existing=True
                    )
                elif reminder == '1d':
                    reminder_time = calculate_reminder_time(day, time_str) - timedelta(days=1)
                    scheduler.add_job(
                        send_reminder,
                        'date',
                        run_date=reminder_time,
                        args=[callback.message.chat.id, f"Завтра в {time_str}: {habit_name}", habit_id, pool],
                        id=f"{habit_id}_{day}_pre1d",
                        replace_existing=True
                    )
        
        # Формируем сообщение о добавленной привычке
        selected_days = sorted([WEEK_DAYS[d] for d in days])
        selected_reminders = [REMINDER_OPTIONS[r] for r in reminders]
        
        message_text = (
            f"✅ Привычка <b>{habit_name}</b> добавлена!\n"
            f"🕘 Время: <b>{time_str}</b>\n"
            f"📅 Дни: {', '.join(selected_days)}"
        )
        
        if selected_reminders:
            message_text += f"\n🔔 Предупредить: {', '.join(selected_reminders)}"
        
        await callback.message.answer(message_text)
        await state.clear()
    
    except Exception as e:
        logging.error(f"Ошибка при добавлении привычки: {e}")
        await callback.message.answer("❌ Произошла ошибка при добавлении привычки")
        await state.clear()
    
    await callback.answer()

# --- Управление привычками ---
@dp.message(Command("myhabits"))
async def cmd_myhabits(message: Message, pool: asyncpg.Pool):
    habits = await pool.fetch("SELECT * FROM habits WHERE user_id = $1", message.from_user.id)
    if not habits:
        await message.answer("📭 У вас нет привычек. Добавьте через /addhabit!")
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for habit in habits:
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"🗑 {habit['name']} ({habit['time']})",
                callback_data=f"delete_{habit['habit_id']}"
            ),
            InlineKeyboardButton(
                text=f"✏️ Редактировать",
                callback_data=f"edit_{habit['habit_id']}"
            )
        ])
    
    await message.answer("📋 Ваши привычки:", reply_markup=kb)

@dp.callback_query(F.data.startswith("delete_"))
async def delete_habit(callback: CallbackQuery, pool: asyncpg.Pool):
    habit_id = int(callback.data.split("_")[1])
    
    # Удаляем из БД
    await pool.execute("DELETE FROM habits WHERE habit_id = $1", habit_id)
    
    # Удаляем все связанные напоминания
    for job in scheduler.get_jobs():
        if str(habit_id) in job.id:
            scheduler.remove_job(job.id)
    
    await callback.message.edit_text("✅ Привычка и все её напоминания удалены!")

# --- Редактирование привычек ---
@dp.message(Command("edithabit"))
async def cmd_edithabit(message: Message, state: FSMContext, pool: asyncpg.Pool):
    habits = await pool.fetch("SELECT * FROM habits WHERE user_id = $1", message.from_user.id)
    if not habits:
        await message.answer("У вас нет привычек для редактирования.")
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for habit in habits:
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{habit['name']} ({habit['time']})",
                callback_data=f"edit_select_{habit['habit_id']}"
            )
        ])
    
    await message.answer("Выберите привычку для редактирования:", reply_markup=kb)
    await state.set_state(EditHabit.select_habit)

@dp.callback_query(EditHabit.select_habit, F.data.startswith("edit_select_"))
async def select_habit_to_edit(callback: CallbackQuery, state: FSMContext, pool: asyncpg.Pool):
    habit_id = int(callback.data.split("_")[2])
    habit = await pool.fetchrow("SELECT * FROM habits WHERE habit_id = $1", habit_id)
    
    await state.update_data(
        habit_id=habit_id,
        current_name=habit['name'],
        current_time=habit['time'],
        current_days=json.loads(habit['days']),
        current_reminders=json.loads(habit['reminders'] or '[]')
    )
    
    await callback.message.answer(
        f"Текущее название: {habit['name']}\n"
        "Введите новое название (или '-' чтобы оставить текущее):"
    )
    await state.set_state(EditHabit.name)
    await callback.answer()

@dp.message(EditHabit.name)
async def edit_habit_name(message: Message, state: FSMContext):
    new_name = message.text if message.text != "-" else None
    await state.update_data(new_name=new_name)
    
    data = await state.get_data()
    await message.answer(
        f"Текущее время: {data['current_time']}\n"
        "Введите новое время (например, 09:00) или '-' чтобы оставить текущее:"
    )
    await state.set_state(EditHabit.time)

@dp.message(EditHabit.time)
async def edit_habit_time(message: Message, state: FSMContext):
    if message.text != "-":
        try:
            hour, minute = map(int, message.text.split(':'))
            dtime(hour=hour, minute=minute)
            new_time = message.text
        except ValueError:
            await message.answer("❌ Неверный формат времени. Введите в формате ЧЧ:ММ")
            return
    else:
        new_time = None
    
    await state.update_data(new_time=new_time)
    data = await state.get_data()
    
    await message.answer(
        "Текущие дни: " + ", ".join([WEEK_DAYS[d] for d in data['current_days']]) + "\n"
        "Выберите новые дни:",
        reply_markup=get_days_keyboard(set(data['current_days']))
    )
    await state.set_state(EditHabit.days)

@dp.callback_query(EditHabit.days, F.data.startswith("day_"))
async def edit_habit_days(callback: CallbackQuery, state: FSMContext):
    day_code = callback.data.split('_')[1]
    data = await state.get_data()
    
    if 'new_days' not in data:
        data['new_days'] = set(data['current_days'])
    
    if day_code == 'everyday':
        data['new_days'] = set(WEEK_DAYS.keys()) - {'everyday'}
    elif day_code in data['new_days']:
        data['new_days'].remove(day_code)
    else:
        data['new_days'].add(day_code)
    
    await state.update_data(new_days=data['new_days'])
    await callback.message.edit_reply_markup(reply_markup=get_days_keyboard(data['new_days']))
    await callback.answer()

@dp.callback_query(EditHabit.days, F.data == "days_done")
async def edit_habit_days_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    if not data.get('new_days'):
        await callback.answer("❌ Нужно выбрать хотя бы один день")
        return
    
    await callback.message.edit_text(
        "Текущие напоминания: " + ", ".join([REMINDER_OPTIONS[r] for r in data['current_reminders']]) + "\n"
        "Выберите новые напоминания:",
        reply_markup=get_reminders_keyboard(set(data['current_reminders']))
    )
    await state.set_state(EditHabit.reminders)
    await callback.answer()

@dp.callback_query(EditHabit.reminders, F.data.startswith("reminder_"))
async def edit_habit_reminders(callback: CallbackQuery, state: FSMContext):
    reminder_code = callback.data.split('_')[1]
    data = await state.get_data()
    
    if 'new_reminders' not in data:
        data['new_reminders'] = set(data['current_reminders'])
    
    if reminder_code == 'none':
        data['new_reminders'] = set()
    elif reminder_code in data['new_reminders']:
        data['new_reminders'].remove(reminder_code)
    else:
        data['new_reminders'].add(reminder_code)
    
    await state.update_data(new_reminders=data['new_reminders'])
    await callback.message.edit_reply_markup(reply_markup=get_reminders_keyboard(data['new_reminders']))
    await callback.answer()

@dp.callback_query(EditHabit.reminders, F.data == "reminders_done")
async def edit_habit_finish(callback: CallbackQuery, state: FSMContext, pool: asyncpg.Pool):
    data = await state.get_data()
    habit_id = data['habit_id']
    
    # Формируем обновленные данные
    updates = {
        "name": data.get('new_name') or data['current_name'],
        "time": data.get('new_time') or data['current_time'],
        "days": json.dumps(list(data.get('new_days') or data['current_days'])),
        "reminders": json.dumps(list(data.get('new_reminders') or data['current_reminders']))
    }
    
    # Обновляем в БД
    await pool.execute(
        """UPDATE habits SET
           name = $1, time = $2, days = $3, reminders = $4
           WHERE habit_id = $5""",
        updates['name'], updates['time'], updates['days'], updates['reminders'], habit_id
    )
    
    # Удаляем старые напоминания
    for job in scheduler.get_jobs():
        if str(habit_id) in job.id:
            scheduler.remove_job(job.id)
    
    # Создаем новые напоминания
    hour, minute = map(int, updates['time'].split(':'))
    days = json.loads(updates['days'])
    reminders = json.loads(updates['reminders'])
    
    for day in days:
        if day == 'everyday':
            continue
            
        scheduler.add_job(
            send_reminder,
            'cron',
            day_of_week=day[:3],
            hour=hour,
            minute=minute,
            args=[callback.message.chat.id, updates['name'], habit_id, pool],
            id=f"{habit_id}_{day}_main",
            replace_existing=True
        )
        
        for reminder in reminders:
            if reminder == '1h' and hour > 0:
                scheduler.add_job(
                    send_reminder,
                    'cron',
                    day_of_week=day[:3],
                    hour=hour-1,
                    minute=minute,
                    args=[callback.message.chat.id, f"Через час: {updates['name']}", habit_id, pool],
                    id=f"{habit_id}_{day}_pre1h",
                    replace_existing=True
                )
            elif reminder == '3h' and hour > 2:
                scheduler.add_job(
                    send_reminder,
                    'cron',
                    day_of_week=day[:3],
                    hour=hour-3,
                    minute=minute,
                    args=[callback.message.chat.id, f"Через 3 часа: {updates['name']}", habit_id, pool],
                    id=f"{habit_id}_{day}_pre3h",
                    replace_existing=True
                )
            elif reminder == '1d':
                reminder_time = calculate_reminder_time(day, updates['time']) - timedelta(days=1)
                scheduler.add_job(
                    send_reminder,
                    'date',
                    run_date=reminder_time,
                    args=[callback.message.chat.id, f"Завтра в {updates['time']}: {updates['name']}", habit_id, pool],
                    id=f"{habit_id}_{day}_pre1d",
                    replace_existing=True
                )
    
    await callback.message.answer("✅ Привычка успешно обновлена!")
    await state.clear()
    await callback.answer()

# --- Напоминания ---
async def send_reminder(chat_id: int, habit: str, habit_id: int, pool: asyncpg.Pool):
    # Проверяем, не выполнена ли привычка сегодня
    completed = await pool.fetchval(
        "SELECT 1 FROM habit_completions WHERE habit_id = $1 AND date = $2",
        habit_id, datetime.now().date()
    )
    
    if not completed:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done_{habit_id}")
        ]])
        await bot.send_message(chat_id, f"🔔 {habit}", reply_markup=kb)

@dp.callback_query(F.data.startswith("done_"))
async def mark_habit_done(callback: CallbackQuery, pool: asyncpg.Pool):
    habit_id = int(callback.data.split("_")[1])
    await pool.execute("""
        INSERT INTO habit_completions (habit_id, date, completed)
        VALUES ($1, $2, TRUE)
        ON CONFLICT (habit_id, date) DO UPDATE SET completed = TRUE
    """, habit_id, datetime.now().date())
    await callback.message.edit_text("👍 Привычка выполнена!")

# --- Статистика ---
@dp.message(Command("stats"))
async def cmd_stats(message: Message, pool: asyncpg.Pool):
    stats = await pool.fetchrow("""
        SELECT 
            COUNT(*) as total_habits,
            SUM(CASE WHEN completed THEN 1 ELSE 0 END) as completed
        FROM habit_completions
        JOIN habits ON habits.habit_id = habit_completions.habit_id
        WHERE habits.user_id = $1
    """, message.from_user.id)

    completion_rate = (stats["completed"] / stats["total_habits"] * 100) if stats["total_habits"] > 0 else 0
    await message.answer(
        f"📊 Ваша статистика:\n"
        f"• Всего привычек: {stats['total_habits']}\n"
        f"• Выполнено: {stats['completed']}\n"
        f"• Процент выполнения: {completion_rate:.1f}%"
    )

@dp.message(Command("weekly_stats"))
async def cmd_weekly_stats(message: Message, pool: asyncpg.Pool):
    stats = await pool.fetch("""
        SELECT 
            date_trunc('day', date) as day,
            COUNT(*) as total,
            SUM(CASE WHEN completed THEN 1 ELSE 0 END) as completed
        FROM habit_completions
        JOIN habits ON habits.habit_id = habit_completions.habit_id
        WHERE habits.user_id = $1
        AND date >= now() - interval '7 days'
        GROUP BY day
        ORDER BY day
    """, message.from_user.id)
    
    if not stats:
        await message.answer("За последнюю неделю данных нет.")
        return
    
    text = "📈 Ваша статистика за неделю:\n\n"
    for day in stats:
        percent = (day['completed'] / day['total'] * 100) if day['total'] > 0 else 0
        text += f"{day['day'].strftime('%d.%m')}: {day['completed']}/{day['total']} ({percent:.0f}%)\n"
    
    await message.answer(text)

@dp.message(Command("daily"))
async def cmd_daily(message: Message, pool: asyncpg.Pool):
    today = datetime.now().strftime("%a").lower()[:3]
    habits = await pool.fetch("""
        SELECT * FROM habits 
        WHERE user_id = $1 
        AND (days LIKE '%' || $2 || '%' OR days LIKE '%everyday%')
    """, message.from_user.id, today)
    
    if not habits:
        await message.answer("Сегодня у вас нет запланированных привычек! 🎉")
        return
    
    text = "📅 Сегодня у вас запланировано:\n\n"
    for habit in habits:
        text += f"• {habit['time']} — {habit['name']}\n"
    
    await message.answer(text)

# --- Резервное копирование ---
@dp.message(Command("backup"))
async def cmd_backup(message: Message, pool: asyncpg.Pool):
    habits = await pool.fetch("SELECT * FROM habits WHERE user_id = $1", message.from_user.id)
    completions = await pool.fetch("""
        SELECT * FROM habit_completions
        JOIN habits ON habits.habit_id = habit_completions.habit_id
        WHERE habits.user_id = $1
    """, message.from_user.id)
    
    backup_data = {
        "habits": [dict(h) for h in habits],
        "completions": [dict(c) for c in completions]
    }
    
    filename = f"backup_{message.from_user.id}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(filename, 'w') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)
    
    with open(filename, 'rb') as f:
        await message.answer_document(f, caption="Ваша резервная копия")
    
    import os
    os.remove(filename)

# --- Запуск бота ---
async def on_startup():
    logging.info("Бот запущен")

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