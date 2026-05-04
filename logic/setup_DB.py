import sqlite3
import os

# The path to your database (going up to the main folder, then into 'data')
DB_NAME = "DataBase/fuel_prices.db"

def create_database():
    print("Starting Database Setup...")
    
    # 1. Create the 'data' folder automatically if it doesn't exist
    os.makedirs(os.path.dirname(DB_NAME), exist_ok=True)
    print("Folder check complete.")

    # 2. Connect to SQLite (This creates the fuel_prices.db file)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 3. Create Regions Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    ''')

    # 4. Create Provinces Table (Linked to Region)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS provinces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            region_id INTEGER,
            FOREIGN KEY (region_id) REFERENCES regions (id)
        )
    ''')

    # 5. Create Cities Table (Linked to Province)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            province_id INTEGER,
            FOREIGN KEY (province_id) REFERENCES provinces (id)
        )
    ''')

    # 6. Create Price Records (The "History" for your AI)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city_id INTEGER,
            fuel_type TEXT,
            brand_name TEXT,
            price_min REAL,
            price_max REAL,
            date_monitored TEXT,
            FOREIGN KEY (city_id) REFERENCES cities (id)
        )
    ''')
    cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                oil_company TEXT,
                effectivity_date TEXT,
                gasoline REAL,
                diesel REAL,
                kerosene REAL,
                date_recorded TEXT
            )
        ''')
    # Save changes and close the connection
    conn.commit()
    conn.close()
    print(f"✅ Database and Tables successfully created at: {DB_NAME}")

if __name__ == "__main__":
    create_database()