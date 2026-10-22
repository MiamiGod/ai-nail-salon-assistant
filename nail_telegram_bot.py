import json
import logging
import os
from datetime import date as date_type, datetime, timedelta, timezone

from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from nail_database import SERVICES, add_booking, cancel_booking, get_bookings, get_free_times, get_user_bookings

SALON_TZ = timezone(timedelta(hours=7))
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = 759399170
if not BOT_TOKEN:
    raise RuntimeError("❌ Не найден BOT_TOKEN!")
if not OPENAI_API_KEY:
    raise RuntimeError("❌ Не найден OPENAI_API_KEY!")
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

CONFIRM_WORDS = {"подтверждаю", "да, подтверждаю", "подтвердить"}
CANCEL_WORDS = {"отмена", "отменить", "не подтверждаю"}
BOOKING_FIELDS = ("name", "service", "date", "time")

BOOKING_TOOL = {"type": "function", "name": "create_booking", "description": "Подготовить запись после сбора данных; Python запросит подтверждение.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "service": {"type": "string", "enum": list(SERVICES)}, "date": {"type": "string", "description": "ДД.ММ.ГГГГ"}, "time": {"type": "string", "description": "ЧЧ:ММ"}}, "required": list(BOOKING_FIELDS), "additionalProperties": False}, "strict": True}
FREE_TIMES_TOOL = {"type": "function", "name": "check_free_times", "description": "Проверить свободное время на конкретную дату.", "parameters": {"type": "object", "properties": {"date": {"type": "string", "description": "ДД.ММ.ГГГГ"}}, "required": ["date"], "additionalProperties": False}, "strict": True}

INSTRUCTIONS = """Ты дружелюбный AI-администратор студии маникюра. Отвечай кратко и по-русски.
Не придумывай свободные интервалы: при вопросах о расписании вызывай check_free_times.
Услуги: Маникюр — 500 ₽; Маникюр с покрытием — 800 ₽; Наращивание ногтей — 1500 ₽; Снятие гель-лака — 300 ₽.
Каждая процедура длится 90 минут, рабочие часы 09:00–20:00. Собери имя, услугу, дату и время.
Дни недели и слова «сегодня», «завтра», «послезавтра» сопоставляй с календарём ниже. Не путай даты между собой.
После сбора данных вызывай create_booking; Python сам попросит подтверждение. Не утверждай, что запись сохранена до успешного результата."""


def salon_now():
    return datetime.now(SALON_TZ).replace(tzinfo=None)


def _normalize_date(value):
    if not isinstance(value, str):
        raise ValueError("Дата должна быть строкой.")
    text = " ".join(value.strip().lower().replace("ё", "е").split())
    now = salon_now().date()
    weekdays = {"понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3, "пятница": 4, "суббота": 5, "воскресенье": 6}
    relatives = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
    if text in relatives:
        return (now + timedelta(days=relatives[text])).strftime("%d.%m.%Y")
    if text in weekdays or (text.startswith("в ") and text[2:] in weekdays):
        target = weekdays[text.removeprefix("в ")]
        days = (target - now.weekday()) % 7
        if days == 0:
            days = 7
        return (now + timedelta(days=days)).strftime("%d.%m.%Y")
    for fmt in ("%d.%m.%Y", "%d.%m"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%d.%m":
                parsed = parsed.replace(year=now.year)
                if parsed.date() < now:
                    parsed = parsed.replace(year=now.year + 1)
            if text != parsed.strftime("%d.%m.%Y") and fmt == "%d.%m.%Y":
                raise ValueError
            if parsed.date() < now:
                raise ValueError("Указанная дата уже прошла.")
            return parsed.strftime("%d.%m.%Y")
        except ValueError:
            continue
    raise ValueError("Неверная дата. Назовите дату, например 23.10.2026 или «завтра».")


def _validate_booking(data):
    booking = {field: str(data.get(field, "")).strip() for field in BOOKING_FIELDS}
    if any(not booking[field] for field in BOOKING_FIELDS):
        raise ValueError("Не заполнены данные записи.")
    if booking["service"] not in SERVICES:
        raise ValueError("Неизвестная услуга.")
    booking["date"] = _normalize_date(booking["date"])
    try:
        parsed_time = datetime.strptime(booking["time"], "%H:%M")
    except ValueError as exc:
        raise ValueError("Неверное время. Используйте ЧЧ:ММ.") from exc
    if booking["time"] != parsed_time.strftime("%H:%M"):
        raise ValueError("Неверное время. Используйте ЧЧ:ММ.")
    if datetime.combine(datetime.strptime(booking["date"], "%d.%m.%Y").date(), parsed_time.time()) <= salon_now():
        raise ValueError("Нельзя записаться на прошедшее время.")
    return booking


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("💅 Добро пожаловать! Напишите, на какую услугу и дату хотите записаться, или используйте /book.")


async def book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = [part.strip() for part in " ".join(context.args).split("|")]
    if len(parts) != 4 or not all(parts):
        await update.message.reply_text("❌ Формат: /book Анна | Маникюр | 27.10.2026 | 12:00")
        return
    try:
        date = _normalize_date(parts[2])
        booking = _validate_booking({"name": parts[0], "service": parts[1], "date": date, "time": parts[3]})
        await update.message.reply_text(add_booking(**booking, telegram_id=update.effective_user.id))
    except ValueError as error:
        await update.message.reply_text(f"❌ {error}")


async def show_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён."); return
    rows = get_bookings()
    await update.message.reply_text("📅 Записей пока нет." if not rows else "\n".join(f"👤 {n}\n💅 {s}\n📅 {d}\n🕒 {t}\n" for n, s, d, t in rows))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bookings = get_user_bookings(update.effective_user.id)
    if context.args:
        try: booking_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Укажите ID записи, например /cancel 2"); return
        booking = next((row for row in bookings if row[0] == booking_id), None)
        if not booking:
            await update.message.reply_text("❌ Запись не найдена или не принадлежит вам."); return
        context.user_data["cancel_id"] = booking_id
        await update.message.reply_text(f"⚠️ Отменить запись №{booking_id}: {booking[2]}, {booking[3]} в {booking[4]}?\nПодтвердите: /confirm_cancel"); return
    await update.message.reply_text("У вас нет записей для отмены." if not bookings else "\n".join(f"№{i} — {s}, {d} в {t}" for i, _, s, d, t in bookings) + "\n\nДля отмены: /cancel ID")


async def confirm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    booking_id = context.user_data.pop("cancel_id", None)
    await update.message.reply_text("❌ Нет записи, ожидающей подтверждения." if booking_id is None else cancel_booking(booking_id, update.effective_user.id))


async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip(); lower = text.lower()
    pending = context.user_data.get("pending_booking")
    if pending:
        if lower in CONFIRM_WORDS:
            result = add_booking(**pending, telegram_id=update.effective_user.id); context.user_data.clear(); await update.message.reply_text(result); return
        if lower in CANCEL_WORDS:
            context.user_data.pop("pending_booking", None); await update.message.reply_text("✅ Подготовленная запись отменена."); return
        context.user_data.pop("pending_booking", None)
    history = context.user_data.setdefault("history", []); history.append({"role": "user", "content": text})
    try:
        response = await ai_client.responses.create(model="gpt-4.1-mini", instructions=INSTRUCTIONS + f"\nСегодня: {salon_now():%d.%m.%Y}, день недели: {['понедельник','вторник','среда','четверг','пятница','суббота','воскресенье'][salon_now().weekday()]}. Часовой пояс: UTC+7.", tools=[BOOKING_TOOL, FREE_TIMES_TOOL], input=history[-20:])
        calls = [item for item in response.output if item.type == "function_call"]
        if calls and calls[0].name == "check_free_times":
            data = json.loads(calls[0].arguments); normalized = _normalize_date(data["date"]); free = get_free_times(normalized)
            message = f"📅 Свободное время на {normalized}:\n\n" + ("\n".join(f"🕒 {t}" for t in free) if free else "😔 свободных интервалов нет.")
            await update.message.reply_text(message); return
        if calls and calls[0].name == "create_booking":
            booking = _validate_booking(json.loads(calls[0].arguments))
            if booking["time"] not in get_free_times(booking["date"]):
                await update.message.reply_text("❌ Выбранное время недоступно. Проверьте расписание ещё раз."); return
            context.user_data["pending_booking"] = booking
            await update.message.reply_text(f"💅 Проверьте запись:\n\n👤 {booking['name']}\n💅 {booking['service']}\n📅 {booking['date']}\n🕒 {booking['time']}\n💰 {SERVICES[booking['service']]} ₽\n\nОтветьте «подтверждаю» или «отмена»."); return
        await update.message.reply_text(response.output_text or "Уточните, пожалуйста, ваш запрос.")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        await update.message.reply_text(f"❌ {error}")
    except Exception:
        logger.exception("Ошибка AI"); await update.message.reply_text("⚠️ Временная ошибка AI. Попробуйте позже.")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("book", book)); app.add_handler(CommandHandler("bookings", show_bookings)); app.add_handler(CommandHandler("cancel", cancel)); app.add_handler(CommandHandler("confirm_cancel", confirm_cancel)); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_reply))
    print("✅ Telegram AI-бот запущен!", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
