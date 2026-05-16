import sqlite3

DB_NAME = 'DataBase/fuel_prices.db'

def get_ncr_fuel_averages():

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  
    cursor = conn.cursor()
    query = """
    WITH latest_report AS (
        SELECT MAX(date_monitored) AS date_monitored
        FROM price_records
        WHERE date_monitored IS NOT NULL
    )
    SELECT 
        pr.fuel_type, 
        ROUND(AVG((pr.price_min + pr.price_max) / 2), 2) AS average_price
    FROM price_records pr
    JOIN cities c ON pr.city_id = c.id
    JOIN provinces p ON c.province_id = p.id
    JOIN regions r ON p.region_id = r.id
    JOIN latest_report lr ON pr.date_monitored = lr.date_monitored
    WHERE r.name = 'NCR'
        AND pr.brand_name != 'OVERALL RANGE'
        AND pr.price_min IS NOT NULL
        AND pr.price_max IS NOT NULL
    GROUP BY pr.fuel_type
    ORDER BY pr.fuel_type;
    """

    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        
 
        averages = [dict(row) for row in rows]
        return averages

    except Exception as e:
        print(f"Error calculating NCR averages: {e}")
        return []
    finally:
        conn.close()

# --- Quick Test ---
if __name__ == "__main__":
    ncr_data = get_ncr_fuel_averages()
    print("NCR Market Snapshot Data:")
    for item in ncr_data:
        print(f"{item['fuel_type']}: ₱{item['average_price']}")
