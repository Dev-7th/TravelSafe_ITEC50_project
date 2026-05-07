import sqlite3

DB_NAME = 'DataBase/fuel_prices.db'

def get_fuel_types():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT DISTINCT fuel_type
            FROM price_records
            WHERE fuel_type IS NOT NULL AND TRIM(fuel_type) != ''
            ORDER BY fuel_type;
        """)
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        print(f"Fuel Type Error: {e}")
        return []
    finally:
        conn.close()

def search_fuel_prices(search_term, fuel_type=None):
    search_term = (search_term or '').strip()
    fuel_type = (fuel_type or '').strip()

    if not search_term:
        return []

    conn = sqlite3.connect('DataBase/fuel_prices.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    search_tokens = [token.lower().replace('ñ', 'n') for token in search_term.split()]
    where_clauses = []
    params = []

    location_search = """
        LOWER(
            REPLACE(
                REPLACE(c.name || ' ' || p.name, 'ñ', 'n'),
                'Ñ',
                'n'
            )
        )
    """

    for token in search_tokens:
        where_clauses.append(f"{location_search} LIKE ?")
        params.append(f"%{token}%")

    if fuel_type:
        where_clauses.append("pr.fuel_type = ?")
        params.append(fuel_type)

    query = """
    SELECT 
        pr.brand_name,
        c.name AS city_name,
        p.name AS province_name,
        pr.fuel_type, 
        ROUND(AVG(pr.price_min), 2) AS price_min,
        ROUND(AVG(pr.price_max), 2) AS price_max,
        ROUND(AVG((pr.price_min + pr.price_max) / 2), 2) AS average_price
    FROM price_records pr
    JOIN cities c ON pr.city_id = c.id
    JOIN provinces p ON c.province_id = p.id
    WHERE pr.brand_name != 'OVERALL RANGE'
    """

    if where_clauses:
        query += " AND " + " AND ".join(where_clauses)

    query += """
    GROUP BY pr.brand_name, c.name, p.name, pr.fuel_type
    ORDER BY p.name, c.name, pr.fuel_type, average_price ASC;
    """

    try:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Search Error: {e}")
        return []
    finally:
        conn.close()
