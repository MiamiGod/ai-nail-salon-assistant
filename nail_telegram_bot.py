
import os
import json
import logging
from datetime import datetime, timedelta
from datetime import timezone

VIETNAM_TZ = timezone(timedelta(hours=7))

def salon_now():
    return datetime.now(VIETNAM_TZ).replace(tzinfo=None)

from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from nail_database import (
    get_free_times,
    add_booking,
    get_bookings,
    get_user_bookings,
    cancel_booking,
    SERVICES,
)


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ADMIN_ID = 759399170

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("❌ Не найден BOT_TOKEN!")

if not OPENAI_API_KEY:
    raise RuntimeError("❌ Не найден OPENAI_API_KEY!")

ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

CONFIRM_WORDS = {
    "подтверждаю",
    "да, подтверждаю",
    "подтвердить",
}

CANCEL_WORDS = {
    "отмена",
    "отменить",
    "не подтверждаю",
}

BOOKING_FIELDS = ("name", "service", "date", "time")


# =========================
# ИНСТРУМЕНТЫ AI
# =========================

BOOKING_TOOL = {
    "type": "function",
    "name": "create_booking",
    "description": (
        "Подготовить запись клиента после сбора имени, "
        "услуги, даты и времени. Python дополнительно "
        "запросит явное подтверждение перед сохранением."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Имя клиента",
            },
            "service": {
                "type": "string",
                "enum": list(SERVICES.keys()),
            },
            "date": {
                "type": "string",
                "description": "Дата ДД.ММ.ГГГГ",
            },
            "time": {
                "type": "string",
                "description": "Время ЧЧ:ММ",
            },
        },
        "required": list(BOOKING_FIELDS),
        "additionalProperties": False,
    },
    "strict": True,
}

FREE_TIMES_TOOL = {
    "type": "function",
    "name": "check_free_times",
    "description": (
        "Получить реальные свободные интервалы "
        "на указанную дату из базы данных SQLite. "
        "Использовать для любого вопроса о свободном времени."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Дата ДД.ММ.ГГГГ",
            },
        },
        "required": ["date"],
        "additionalProperties": False,
    },
    "strict": True,
}


# =========================
# ИНСТРУКЦИИ AI
# =========================

INSTRUCTIONS = """
Ты дружелюбный AI-администратор студии маникюра.
Общайся кратко, естественно и на русском языке.

Услуги:
- Маникюр — 500 рублей.
- Маникюр с покрытием — 800 рублей.
- Наращивание ногтей — 1500 рублей.
- Снятие гель-лака — 300 рублей.

Рабочие часы: 09:00–20:00.
Пока все процедуры занимают 90 минут.

Правила:
1. Не придумывай свободное время.
2. При вопросе о свободных часах обязательно
   вызывай check_free_times.
3. Не называй свободные часы из памяти.
4. Если клиент назвал дату без года, используй
   ближайшую будущую такую дату.
5. Не предлагай прошедшие даты.
6. Для записи собери имя, услугу, дату и время.
7. Не спрашивай повторно уже известные данные.
8. Когда все данные собраны, вызывай create_booking.
9. Python сам попросит клиента подтвердить запись.
10. Не утверждай, что запись сохранена,
    пока Python не подтвердил результат.
11. Если клиент меняет данные, учитывай изменения.
12. Если инструмент вернул ошибку, сообщи её.
"""


def current_instructions():
    now = datetime.now()

    return (
        INSTRUCTIONS
        + f"\nСегодня: {now.strftime('%d.%m.%Y')}."
        + f"\nТекущий год: {now.year}."
        + "\nИспользуй эти данные при определении дат."
    )


# =========================
# ПРОВЕРКА ДАТЫ
# =========================

def normalize_date(value):
    if not isinstance(value, str):
        raise ValueError("Дата должна быть строкой.")

    value = value.strip()
    now = datetime.now()

    # Поддержка дат вида 23.10
    try:
        day_month = datetime.strptime(value, "%d.%m")
    except ValueError:
        day_month = None

    if day_month is not None:
        day = day_month.day
        month = day_month.month

        for year in range(now.year, now.year + 5):
            try:
                candidate = datetime(year, month, day).date()
            except ValueError:
                continue

            if candidate >= now.date():
                return candidate.strftime("%d.%m.%Y")

        raise ValueError("Не удалось определить дату.")

    # Полная дата: 23.10.2026
    try:
        parsed = datetime.strptime(
            value, "%d.%m.%Y"
        ).date()
    except ValueError:
        raise ValueError(
            "Неверная дата. Используйте ДД.ММ.ГГГГ."
        )

    if value != parsed.strftime("%d.%m.%Y"):
        raise ValueError("Неверный формат даты.")

    if parsed < now.date():
        raise ValueError(
            "Указанная дата уже прошла. "
            "Назовите будущую дату."
        )

    return parsed.strftime("%d.%m.%Y")


def validate_booking_data(data):
    booking = {}

    for field in BOOKING_FIELDS:
        value = data.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Не заполнено поле: {field}"
            )

        booking[field] = value.strip()

    if booking["service"] not in SERVICES:
        raise ValueError("Неизвестная услуга.")

    booking["date"] = normalize_date(
        booking["date"]
    )

    try:
        parsed_time = datetime.strptime(
            booking["time"], "%H:%M"
        )
    except ValueError:
        raise ValueError("Неверный формат времени.")

    if booking["time"] != parsed_time.strftime("%H:%M"):
        raise ValueError("Неверный формат времени.")

    booking_datetime = datetime.combine(
        datetime.strptime(
            booking["date"], "%d.%m.%Y"
        ).date(),
        parsed_time.time(),
    )

    if booking_datetime <= datetime.now():
        raise ValueError(
            "Нельзя записаться на прошедшее время."
        )

    return booking


# =========================
# TELEGRAM-КОМАНДЫ
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("history", None)
    context.user_data.pop("pending_booking", None)
    context.user_data.pop("cancel_id", None)

    await update.message.reply_text(
        "💅 Добро пожаловать в студию маникюра!\n\n"
        "Маникюр — 500 ₽\n"
        "Маникюр с покрытием — 800 ₽\n"
        "Наращивание ногтей — 1500 ₽\n"
        "Снятие гель-лака — 300 ₽\n\n"
        "🕒 Работаем с 09:00 до 20:00.\n\n"
        "Напишите, например:\n"
        "«Хочу записаться на маникюр завтра».\n\n"
        "Или используйте /book."
    )


async def book(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    parts = [
        part.strip()
        for part in " ".join(context.args).split("|")
    ]

    if len(parts) != 4 or not all(parts):
        await update.message.reply_text(
            "❌ Неверный формат.\n\n"
            "/book Анна | Маникюр | "
            "27.10.2026 | 12:00"
        )
        return

    name, service, date, time = parts

    try:
        date = normalize_date(date)
    except ValueError as error:
        await update.message.reply_text(
            f"❌ {error}"
        )
        return

    result = add_booking(
        name,
        service,
        date,
        time,
        telegram_id=update.effective_user.id,
    )

    await update.message.reply_text(result)


async def show_bookings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ Доступ запрещён."
        )
        return

    bookings = get_bookings()

    if not bookings:
        await update.message.reply_text(
            "📅 Записей пока нет."
        )
        return

    lines = ["💅 Записи клиентов:\n"]

    for name, service, date, time in bookings:
        lines.append(
            f"👤 {name}\n"
            f"💅 {service}\n"
            f"📅 {date}\n"
            f"🕒 {time}\n"
        )

    message = "\n".join(lines)

    for i in range(0, len(message), 4000):
        await update.message.reply_text(
            message[i:i + 4000]
        )


# =========================
# AI-ПЕРЕПИСКА
# =========================

async def ai_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_message = update.message.text.strip()
    user_lower = user_message.lower()

    # Подтверждение обрабатывает Python,
    # а не AI. Это защищает от случайной записи.
    pending = context.user_data.get(
        "pending_booking"
    )

    if pending is not None:
        if user_lower in CONFIRM_WORDS:
            result = add_booking(
                **pending,
                telegram_id=update.effective_user.id,
            )

            context.user_data.pop(
                "pending_booking", None
            )
            context.user_data["history"] = []

            await update.message.reply_text(result)
            return

        if user_lower in CANCEL_WORDS:
            context.user_data.pop(
                "pending_booking", None
            )
            context.user_data["history"] = []

            await update.message.reply_text(
                "✅ Подготовленная запись отменена."
            )
            return

        # Клиент может изменить детали,
        # поэтому старая заявка больше не действует.
        context.user_data.pop(
            "pending_booking", None
        )

    history = context.user_data.setdefault(
        "history", []
    )

    history.append({
        "role": "user",
        "content": user_message,
    })

    try:
        response = await ai_client.responses.create(
            model="gpt-4.1-mini",
            instructions=current_instructions(),
            tools=[BOOKING_TOOL, FREE_TIMES_TOOL],
            input=history[-20:],
        )

        tool_calls = [
            item
            for item in response.output
            if item.type == "function_call"
        ]

        # Сначала обрабатываем запросы расписания.
        free_calls = [
            item
            for item in tool_calls
            if item.name == "check_free_times"
        ]

        if free_calls:
            tool_call = free_calls[0]

            try:
                data = json.loads(
                    tool_call.arguments
                )

                date = normalize_date(
                    data["date"]
                )

                free_times = get_free_times(date)

                if free_times:
                    # Предлагаем первые три варианта.
                    suggestions = free_times[:3]

                    message = (
                        f"📅 Свободное время "
                        f"на {date}:\n\n"
                        + "\n".join(
                            f"🕒 {time}"
                            for time in suggestions
                        )
                    )

                    if len(free_times) > 3:
                        message += (
                            "\n\nЕсть и другие "
                            "свободные часы."
                        )

                    message += (
                        "\n\nКакое время "
                        "вам подходит?"
                    )

                else:
                    message = (
                        f"😔 На {date} свободных "
                        "интервалов нет.\n"
                        "Можем выбрать другую дату."
                    )

            except (
                ValueError,
                KeyError,
                TypeError,
                json.JSONDecodeError,
            ) as error:
                message = (
                    f"❌ Не удалось проверить "
                    f"расписание: {error}"
                )

            # Сохраняем ответ в историю,
            # чтобы AI помнил предложенные часы.
            history.append({
                "role": "assistant",
                "content": message,
            })

            context.user_data["history"] = (
                history[-20:]
            )

            await update.message.reply_text(
                message
            )
            return

        # Обрабатываем создание записи.
        booking_calls = [
            item
            for item in tool_calls
            if item.name == "create_booking"
        ]

        if booking_calls:
            tool_call = booking_calls[0]

            try:
                data = json.loads(
                    tool_call.arguments
                )

                booking = validate_booking_data(
                    data
                )

            except (
                ValueError,
                KeyError,
                TypeError,
                json.JSONDecodeError,
            ) as error:
                message = (
                    f"❌ Не удалось подготовить "
                    f"запись: {error}"
                )

                history.append({
                    "role": "assistant",
                    "content": message,
                })

                context.user_data["history"] = (
                    history[-20:]
                )

                await update.message.reply_text(
                    message
                )
                return

            # Предварительно проверяем
            # свободное время через SQLite.
            free_times = get_free_times(
                booking["date"]
            )

            if booking["time"] not in free_times:
                message = (
                    "❌ Выбранное время недоступно "
                    "в расписании.\n"
                    "Выберите другой свободный "
                    "интервал."
                )

                history.append({
                    "role": "assistant",
                    "content": message,
                })

                context.user_data["history"] = (
                    history[-20:]
                )

                await update.message.reply_text(
                    message
                )
                return

            context.user_data[
                "pending_booking"
            ] = booking

            message = (
                "💅 Проверьте запись:\n\n"
                f"👤 {booking['name']}\n"
                f"💅 {booking['service']}\n"
                f"📅 {booking['date']}\n"
                f"🕒 {booking['time']}\n"
                "⏱ Продолжительность: 90 минут\n"
                f"💰 Цена: "
                f"{SERVICES[booking['service']]} ₽\n\n"
                "Если всё верно, ответьте:\n"
                "«подтверждаю»\n\n"
                "Для отказа напишите «отмена»."
            )

            history.append({
                "role": "assistant",
                "content": message,
            })

            context.user_data["history"] = (
                history[-20:]
            )

            await update.message.reply_text(
                message
            )
            return

        # Обычный ответ AI.
        answer = response.output_text

        if not answer:
            answer = (
                "Уточните, пожалуйста, "
                "ваш запрос."
            )

        history.append({
            "role": "assistant",
            "content": answer,
        })

        context.user_data["history"] = (
            history[-20:]
        )

        await update.message.reply_text(
            answer
        )

    except Exception:
        logger.exception("Ошибка AI")

        context.user_data["history"] = (
            history[:-1][-20:]
        )

        await update.message.reply_text(
            "⚠️ Временная ошибка AI. "
            "Попробуйте позже."
        )


# =========================
# ОТМЕНА ЗАПИСЕЙ
# =========================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    bookings = get_user_bookings(user_id)

    if context.args:
        try:
            booking_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(
                "❌ Укажите номер записи.\n"
                "Например: /cancel 2"
            )
            return

        booking = next(
            (
                b for b in bookings
                if b[0] == booking_id
            ),
            None,
        )

        if booking is None:
            await update.message.reply_text(
                "❌ Запись не найдена "
                "или не принадлежит вам."
            )
            return

        context.user_data[
            "cancel_id"
        ] = booking_id

        await update.message.reply_text(
            f"⚠️ Отменить запись №{booking_id}?\n"
            f"💅 {booking[2]}\n"
            f"📅 {booking[3]} "
            f"в {booking[4]}\n\n"
            "Для подтверждения отправьте "
            "/confirm_cancel"
        )
        return

    if not bookings:
        await update.message.reply_text(
            "У вас нет записей для отмены."
        )
        return

    lines = ["Ваши записи:\n"]

    for booking_id, name, service, date, time in bookings:
        lines.append(
            f"№{booking_id} — "
            f"{service}, {date} в {time}"
        )

    lines.append(
        "\nДля отмены отправьте /cancel ID"
    )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def confirm_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    booking_id = context.user_data.pop(
        "cancel_id", None
    )

    if booking_id is None:
        await update.message.reply_text(
            "❌ Нет записи, ожидающей подтверждения."
        )
        return

    result = cancel_booking(
        booking_id,
        update.effective_user.id,
    )

    await update.message.reply_text(result)


# =========================
# ОБРАБОТКА ОШИБОК
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Ошибка Telegram-бота",
        exc_info=context.error,
    )


# =========================
# ЗАПУСК
# =========================

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "confirm_cancel",
            confirm_cancel,
        )
    )

    app.add_handler(
        CommandHandler("cancel", cancel)
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler(
            "bookings",
            show_bookings,
        )
    )

    app.add_handler(
        CommandHandler("book", book)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            ai_reply,
        )
    )

    app.add_error_handler(error_handler)

    print("✅ Telegram AI-бот запущен!")

    app.run_polling()


if __name__ == "__main__":
    main()
