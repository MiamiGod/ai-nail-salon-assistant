import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SALON_TZ = timezone(timedelta(hours=7))
DB_PATH = Path(__file__).resolve().parent / "nail_bookings.db"
SERVICES = {
    "Маникюр": 500,
    "Маникюр с покрытием": 800,
    "Наращивание ногтей": 1500,
    "Снятие гель-лака": 300,
}
DURATION = timedelta(minutes=90)
OPEN_TIME = "09:00"
CLOSE_TIME = "20:00"


def salon_now():
    return datetime.now(SALON_TZ).replace(tzinfo=None)


def _parse_date(value):
    parsed = datetime.strptime(value, "%d.%m.%Y").date()
    if value != parsed.strftime("%d.%m.%Y"):
        raise ValueError("Неверный формат даты.")
    return parsed


def _parse_time(value):
    parsed = datetime.strptime(value, "%H:%M").time()
    if value != parsed.strftime("%H:%M"):
        raise ValueError("Неверный формат времени.")
    return parsed


def init_db():
    # Совместимо со старой схемой; существующие записи и их ID не меняются.
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                service TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                UNIQUE(date, time)
            )
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(bookings)")}
        if "telegram_id" not in columns:
            connection.execute("ALTER TABLE bookings ADD COLUMN telegram_id INTEGER")


def _busy_times(connection, date):
    return [row[0] for row in connection.execute(
        "SELECT time FROM bookings WHERE date = ? ORDER BY time", (date,)
    )]


def add_booking(name, service, date, time, telegram_id=None):
    name, service, date, time = name.strip(), service.strip(), date.strip(), time.strip()
    if not name:
        return "❌ Укажите имя клиента!"
    if service not in SERVICES:
        return "❌ Такой услуги нет в нашем салоне!"
    try:
        booking_date = _parse_date(date)
        booking_time = _parse_time(time)
    except ValueError:
        return "❌ Проверьте дату ДД.ММ.ГГГГ и время ЧЧ:ММ."

    start = datetime.combine(booking_date, booking_time)
    end = start + DURATION
    opening = datetime.combine(booking_date, _parse_time(OPEN_TIME))
    closing = datetime.combine(booking_date, _parse_time(CLOSE_TIME))
    if start < opening or end > closing:
        return "❌ Процедура должна проходить с 09:00 до 20:00!"
    if start <= salon_now():
        return "❌ Нельзя записаться на прошедшую дату или время!"

    try:
        with sqlite3.connect(DB_PATH, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for busy in _busy_times(connection, date):
                busy_start = datetime.combine(booking_date, _parse_time(busy))
                if start < busy_start + DURATION and busy_start < end:
                    return "❌ Это время занято. Выберите другое."
            connection.execute(
                "INSERT INTO bookings (name, service, date, time, telegram_id) VALUES (?, ?, ?, ?, ?)",
                (name, service, date, time, telegram_id),
            )
        return f"✅ Клиент {name} успешно записан!"
    except sqlite3.IntegrityError:
        return "❌ Это время уже занято!"
    except sqlite3.OperationalError:
        return "❌ База временно недоступна. Попробуйте позже."


def get_bookings():
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute("SELECT name, service, date, time FROM bookings ORDER BY id").fetchall()


def get_user_bookings(telegram_id):
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute(
            "SELECT id, name, service, date, time FROM bookings WHERE telegram_id = ? ORDER BY id",
            (telegram_id,),
        ).fetchall()


def cancel_booking(booking_id, telegram_id):
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            "DELETE FROM bookings WHERE id = ? AND telegram_id = ?", (booking_id, telegram_id)
        )
        return "✅ Запись успешно отменена!" if cursor.rowcount else "❌ Запись не найдена или принадлежит другому клиенту."


def get_busy_times(date):
    with sqlite3.connect(DB_PATH) as connection:
        return _busy_times(connection, date)


def get_free_times(date):
    try:
        booking_date = _parse_date(date)
    except ValueError:
        return []
    with sqlite3.connect(DB_PATH) as connection:
        busy_times = _busy_times(connection, date)
    result = []
    current = datetime.combine(booking_date, _parse_time(OPEN_TIME))
    closing = datetime.combine(booking_date, _parse_time(CLOSE_TIME))
    now = salon_now()
    while current + DURATION <= closing:
        slot_end = current + DURATION
        overlap = any(
            current < datetime.combine(booking_date, _parse_time(busy)) + DURATION
            and datetime.combine(booking_date, _parse_time(busy)) < slot_end
            for busy in busy_times
        )
        if not overlap and current > now:
            result.append(current.strftime("%H:%M"))
        current += DURATION
    return result


init_db()
