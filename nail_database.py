import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

VIETNAM_TZ = timezone(timedelta(hours=7))

DB_PATH = Path(__file__).resolve().parent / "nail_bookings.db"


def salon_now():
    return datetime.now(VIETNAM_TZ).replace(tzinfo=None)


SERVICES = {
    "Маникюр": 500,
    "Маникюр с покрытием": 800,
    "Наращивание ногтей": 1500,
    "Снятие гель-лака": 300,
}

DURATION = timedelta(minutes=90)


def init_db():
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

        columns = connection.execute(
            "PRAGMA table_info(bookings)"
        ).fetchall()

        column_names = [column[1] for column in columns]

        if "telegram_id" not in column_names:
            connection.execute(
                "ALTER TABLE bookings ADD COLUMN telegram_id INTEGER"
            )


def add_booking(name, service, date, time, telegram_id=None):
    name = name.strip()
    service = service.strip()
    date = date.strip()
    time = time.strip()

    if not name:
        return "❌ Укажите имя клиента!"

    if service not in SERVICES:
        return "❌ Такой услуги нет в нашем салоне!"

    try:
        booking_date = datetime.strptime(
            date, "%d.%m.%Y"
        ).date()
    except ValueError:
        return "❌ Неверная дата! Используйте ДД.ММ.ГГГГ."

    if date != booking_date.strftime("%d.%m.%Y"):
        return "❌ Неверная дата! Используйте ДД.ММ.ГГГГ."

    try:
        booking_time = datetime.strptime(
            time, "%H:%M"
        ).time()
    except ValueError:
        return "❌ Неверное время! Используйте ЧЧ:ММ."

    if time != booking_time.strftime("%H:%M"):
        return "❌ Неверное время! Используйте ЧЧ:ММ."

    booking_datetime = datetime.combine(
        booking_date, booking_time
    )
    booking_end = booking_datetime + DURATION

    opening = datetime.combine(
        booking_date,
        datetime.strptime("09:00", "%H:%M").time()
    )

    closing = datetime.combine(
        booking_date,
        datetime.strptime("20:00", "%H:%M").time()
    )

    if booking_datetime < opening or booking_end > closing:
        return "❌ Процедура должна проходить с 09:00 до 20:00!"

    if booking_datetime <= salon_now():
        return "❌ Нельзя записаться на прошедшую дату или время!"

    try:
        with sqlite3.connect(DB_PATH, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")

            existing_times = connection.execute(
                """
                SELECT time
                FROM bookings
                WHERE date = ?
                """,
                (date,)
            ).fetchall()

            for (existing_time,) in existing_times:
                existing_start = datetime.combine(
                    booking_date,
                    datetime.strptime(
                        existing_time, "%H:%M"
                    ).time()
                )

                existing_end = existing_start + DURATION

                if (
                    booking_datetime < existing_end
                    and existing_start < booking_end
                ):
                    return "❌ Это время занято. Выберите другое."

            connection.execute(
                """
                INSERT INTO bookings
                (name, service, date, time, telegram_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, service, date, time, telegram_id)
            )

        return f"✅ Клиент {name} успешно записан!"

    except sqlite3.IntegrityError:
        return "❌ Это время уже занято!"

    except sqlite3.OperationalError:
        return "❌ База временно недоступна. Попробуйте позже."


def get_bookings():
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute(
            """
            SELECT name, service, date, time
            FROM bookings
            ORDER BY id
            """
        ).fetchall()


def cancel_booking(booking_id, telegram_id):
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            DELETE FROM bookings
            WHERE id = ? AND telegram_id = ?
            """,
            (booking_id, telegram_id)
        )

        if cursor.rowcount == 0:
            return "❌ Запись не найдена или принадлежит другому клиенту."

        return "✅ Запись успешно отменена!"


def get_user_bookings(telegram_id):
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute(
            """
            SELECT id, name, service, date, time
            FROM bookings
            WHERE telegram_id = ?
            ORDER BY id
            """,
            (telegram_id,)
        ).fetchall()


def get_busy_times(date):
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT time
            FROM bookings
            WHERE date = ?
            ORDER BY time
            """,
            (date,)
        ).fetchall()

    return [row[0] for row in rows]


def get_free_times(date):
    try:
        booking_date = datetime.strptime(
            date, "%d.%m.%Y"
        ).date()
    except ValueError:
        return []

    if date != booking_date.strftime("%d.%m.%Y"):
        return []

    busy_times = get_busy_times(date)
    free_times = []

    current = datetime.combine(
        booking_date,
        datetime.strptime("09:00", "%H:%M").time()
    )

    closing = datetime.combine(
        booking_date,
        datetime.strptime("20:00", "%H:%M").time()
    )

    now = salon_now()

    while current + DURATION <= closing:
        slot_end = current + DURATION

        overlap = any(
            current < datetime.combine(
                booking_date,
                datetime.strptime(t, "%H:%M").time()
            ) + DURATION
            and
            datetime.combine(
                booking_date,
                datetime.strptime(t, "%H:%M").time()
            ) < slot_end
            for t in busy_times
        )

        if not overlap and current > now:
            free_times.append(current.strftime("%H:%M"))

        current += DURATION

    return free_times


init_db()


if __name__ == "__main__":
    print("✅ База данных готова!")
    print("Записи:", get_bookings())
