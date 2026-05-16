import sqlite3
from datetime import datetime

DB_NAME = "DataBase/fuel_prices.db"

def find_existing_id(cursor, table, name, parent_id=None, parent_col=None):
    name = (name or "").strip()
    if not name:
        return None

    if parent_id and parent_col:
        cursor.execute(
            f"""
            SELECT id FROM {table}
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
              AND {parent_col} = ?
            """,
            (name, parent_id),
        )
    else:
        cursor.execute(
            f"""
            SELECT id FROM {table}
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
            """,
            (name,),
        )

    result = cursor.fetchone()
    return result[0] if result else None

def get_existing_location_ids(cursor, record):
    region_id = find_existing_id(cursor, "regions", record.get("category"))
    if not region_id:
        return None

    province_id = find_existing_id(
        cursor,
        "provinces",
        record.get("province"),
        region_id,
        "region_id",
    )
    if not province_id:
        return None

    city_id = find_existing_id(
        cursor,
        "cities",
        record.get("city"),
        province_id,
        "province_id",
    )
    if not city_id:
        return None

    cursor.execute(
        "SELECT 1 FROM price_records WHERE city_id = ? LIMIT 1",
        (city_id,),
    )
    if not cursor.fetchone():
        return None

    return region_id, province_id, city_id

def save_fuel_data(data_list):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # List of brands to look for in your dictionary
    brands = ["petron", "shell", "caltex", "phoenix", "unioil", "seaoil", "total", "ptt", "flying_v", "independent", "overall_range"]

    for record in data_list:
        try:
            location_ids = get_existing_location_ids(cursor, record)
            if not location_ids:
                print(
                    "⚠️ Skipped unknown location: "
                    f"{record.get('category')} / {record.get('province')} / {record.get('city')}"
                )
                continue

            _, _, city_id = location_ids
            
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
