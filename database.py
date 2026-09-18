import sqlite3


DB_NAME = "bizbot_v06.db"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    conn = get_connection()
    cursor = conn.cursor()

    # -------------------------
    # BUSINESSES
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_telegram_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            city TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -------------------------
    # SERVICES
    # -------------------------

   
    # -------------------------
    # SERVICES
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            duration INTEGER NOT NULL,
            FOREIGN KEY (business_id)
            REFERENCES businesses(id)
        )
    """)

    # -------------------------
    # WORKING HOURS
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS working_hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            weekday INTEGER NOT NULL,
            start_time TEXT,
            end_time TEXT,
            is_open INTEGER DEFAULT 1,

            FOREIGN KEY (business_id)
            REFERENCES businesses(id)
        )
    """)

    # -------------------------
    # CUSTOMERS
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            name TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (business_id)
            REFERENCES businesses(id)
        )
    """)

    # -------------------------
    # BOOKINGS
    # -------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            customer_id INTEGER,
            service_id INTEGER NOT NULL,
            booking_date TEXT NOT NULL,
            booking_time TEXT NOT NULL,
            status TEXT DEFAULT 'confirmed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (business_id)
            REFERENCES businesses(id),

            FOREIGN KEY (customer_id)
            REFERENCES customers(id),

            FOREIGN KEY (service_id)
            REFERENCES services(id)
        )
    """)
    
    
    conn.commit()
    conn.close()
def set_business_schedule(
    business_id,
    weekday,
    open_time,
    close_time,
    is_working=1
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO business_schedule (
            business_id,
            weekday,
            open_time,
            close_time,
            is_working
        )
        VALUES (?, ?, ?, ?, ?)

        ON CONFLICT(business_id, weekday)
        DO UPDATE SET
            open_time = excluded.open_time,
            close_time = excluded.close_time,
            is_working = excluded.is_working
        """,
        (
            business_id,
            weekday,
            open_time,
            close_time,
            is_working
        )
    )

    conn.commit()
    conn.close()
def get_business_schedule(business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT weekday, open_time, close_time, is_working
        FROM business_schedule
        WHERE business_id = ?
        ORDER BY weekday
        """,
        (business_id,)
    )

    schedule = cursor.fetchall()
    conn.close()

    return schedule

if __name__ == "__main__":
    init_database()
    print("✅ BizBot v0.6 database created!")

def set_working_hours(
    business_id,
    weekday,
    start_time,
    end_time,
    is_open=1
):
    conn = get_connection()
    cursor = conn.cursor()

    # Прибираємо старий графік цього дня
    cursor.execute(
        """
        DELETE FROM working_hours
        WHERE business_id = ? AND weekday = ?
        """,
        (business_id, weekday)
    )

    # Записуємо новий
    cursor.execute(
        """
        INSERT INTO working_hours (
            business_id,
            weekday,
            start_time,
            end_time,
            is_open
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            business_id,
            weekday,
            start_time,
            end_time,
            is_open
        )
    )

    conn.commit()
    conn.close()


def get_working_hours(business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT weekday, start_time, end_time, is_open
        FROM working_hours
        WHERE business_id = ?
        ORDER BY weekday
        """,
        (business_id,)
    )

    rows = cursor.fetchall()
    conn.close()

    return rows