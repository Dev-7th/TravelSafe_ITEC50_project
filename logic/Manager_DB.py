import sqlite3
from datetime import datetime

DB_NAME = "DataBase/fuel_prices.db"

def get_or_create_id(cursor, table, name, parent_id=None, parent_col=None):
    if parent_id and parent_col:
        # Check with parent link (e.g., City 'Bacoor' in Province 'Cavite')
        cursor.execute(f"SELECT id FROM {table} WHERE name = ? AND {parent_col} = ?", (name, parent_id))
    else:
        # Check top level (e.g., Region 'SOUTH LUZON')
        cursor.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
    result = cursor.fetchone()
    if result:
        return result[0]
    else:
        if parent_id and parent_col:
            # Insert with parent link (e.g., City 'Bacoor' in Province 'Cavite')
            cursor.execute(f"INSERT INTO {table} (name, {parent_col}) VALUES (?, ?)", (name, parent_id))
        else:
            # Insert top level (e.g., Region 'SOUTH LUZON')
            cursor.execute(f"INSERT INTO {table} (name) VALUES (?)", (name,))
        return cursor.lastrowid

def save_fuel_data(data_list):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # List of brands to look for in your dictionary
    brands = ["petron", "shell", "caltex", "phoenix", "unioil", "seaoil", "total", "ptt", "flying_v", "independent", "overall_range"]

    for record in data_list:
        try:
            # 1. Handle Hierarchy
            region_id = get_or_create_id(cursor, "regions", record['category'])
            province_id = get_or_create_id(cursor, "provinces", record['province'], region_id, "region_id")
            city_id = get_or_create_id(cursor, "cities", record['city'], province_id, "province_id")
            
            # 2. Handle Prices for each Brand
            for brand in brands:
                min_price = record.get(f"{brand}_min")
                max_price = record.get(f"{brand}_max")
                
                # Only insert if there's an actual price (skip nulls)
                if min_price is not None:
                    cursor.execute('''
                        INSERT INTO price_records (city_id, fuel_type, brand_name, price_min, price_max, date_monitored)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        city_id, 
                        record['product'], 
                        brand.upper().replace('_', ' '), 
                        min_price, 
                        max_price,
                        datetime.now().strftime("%Y-%m-%d") # Automatically add today's date
                    ))
            
            print(f"✅ Saved: {record['city']} - {record['product']}")
            
        except Exception as e:
            print(f"❌ Error saving record: {e}")
            continue
            
    conn.commit()
    conn.close()
    print("\n--- All data committed to Database ---")

    
def save_adjustment_data(adjustment_list):
    """Saves the national price adjustments to the database."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    for record in adjustment_list:
        try:
            # Convert 'null' (None in Python) to 0.0 for the AI
            gas_change = float(record.get('gasoline')) if record.get('gasoline') is not None else 0.0
            diesel_change = float(record.get('diesel')) if record.get('diesel') is not None else 0.0
            kero_change = float(record.get('kerosene')) if record.get('kerosene') is not None else 0.0

            cursor.execute('''
                INSERT INTO price_adjustments (oil_company, effectivity_date, gasoline, diesel, kerosene, date_recorded)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                record.get('oil_company', 'UNKNOWN'),
                record.get('date_time_of_effectivity', 'UNKNOWN'),
                gas_change,
                diesel_change,
                kero_change,
                datetime.now().strftime("%Y-%m-%d") # Save today's date so you know when you scraped it
            ))
            
            print(f"✅ Adjustment Saved: {record['oil_company']} (Gas: {gas_change}, Diesel: {diesel_change})")

        except Exception as e:
            print(f"❌ Error saving adjustment record: {e}")
            continue

    conn.commit()
    conn.close()
    print("\n--- All Adjustment data committed to Database ---")