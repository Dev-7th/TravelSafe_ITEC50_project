import re
import sqlite3

DB_NAME = "DataBase/fuel_prices.db"


def _normalize_location(value):
    value = (value or "").strip().lower()
    value = value.replace("ñ", "n").replace("Ñ", "n")
    value = re.sub(r"\b(city|municipality|mun\.|province|of)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _price_average(price_min, price_max):
    if price_min is None and price_max is None:
        return None
    if price_min is None:
        return float(price_max)
    if price_max is None:
        return float(price_min)
    return round((float(price_min) + float(price_max)) / 2, 2)


def _format_brand_prices(rows):
    brands = {}
    lowest_price = None
    lowest_brands = set()

    for row in rows:
        brand_name = row["brand_name"]
        average_price = _price_average(row["price_min"], row["price_max"])

        brands.setdefault(
            brand_name,
            {
                "brand_name": brand_name,
                "lowest_price": None,
                "fuel_types": [],
            },
        )

        brands[brand_name]["fuel_types"].append(
            {
                "fuel_type": row["fuel_type"],
                "price_min": row["price_min"],
                "price_max": row["price_max"],
                "average_price": average_price,
                "date_monitored": row["date_monitored"],
            }
        )

        if average_price is None:
            continue

        brand_lowest = brands[brand_name]["lowest_price"]
        if brand_lowest is None or average_price < brand_lowest:
            brands[brand_name]["lowest_price"] = average_price

        if lowest_price is None or average_price < lowest_price:
            lowest_price = average_price
            lowest_brands = {brand_name}
        elif average_price == lowest_price:
            lowest_brands.add(brand_name)

    for brand in brands.values():
        brand["fuel_types"].sort(key=lambda item: (item["fuel_type"] or "", item["average_price"] or 0))

    return brands, lowest_price, sorted(lowest_brands)


def _find_city(cursor, city_name):
    normalized_city = _normalize_location(city_name)
    if not normalized_city:
        return None

    cursor.execute(
        """
        SELECT c.id, c.name AS city_name, p.name AS province_name
        FROM cities c
        JOIN provinces p ON c.province_id = p.id
        """
    )

    exact_matches = []
    partial_matches = []
    for row in cursor.fetchall():
        db_city = _normalize_location(row["city_name"])
        if db_city == normalized_city:
            exact_matches.append(row)
        elif normalized_city in db_city or db_city in normalized_city:
            partial_matches.append(row)

    matches = exact_matches or partial_matches
    return matches[0] if matches else None


def get_latest_city_fuel_prices(city_name):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        city = _find_city(cursor, city_name)
        if not city:
            return {
                "found": False,
                "message": f"No fuel price records found for '{city_name}'.",
                "query_city": city_name,
                "brands": {},
                "lowest_price": None,
                "lowest_brands": [],
            }

        cursor.execute(
            """
            SELECT MAX(date_monitored)
            FROM price_records
            WHERE city_id = ?
              AND brand_name != 'OVERALL RANGE'
            """,
            (city["id"],),
        )
        latest_date = cursor.fetchone()[0]

        if not latest_date:
            return {
                "found": False,
                "message": f"No fuel price records found for '{city['city_name']}'.",
                "query_city": city_name,
                "city": city["city_name"],
                "province": city["province_name"],
                "brands": {},
                "lowest_price": None,
                "lowest_brands": [],
            }

        cursor.execute(
            """
            SELECT
                brand_name,
                fuel_type,
                ROUND(AVG(price_min), 2) AS price_min,
                ROUND(AVG(price_max), 2) AS price_max,
                date_monitored
            FROM price_records
            WHERE city_id = ?
              AND date_monitored = ?
              AND brand_name != 'OVERALL RANGE'
            GROUP BY brand_name, fuel_type, date_monitored
            ORDER BY brand_name, fuel_type
            """,
            (city["id"], latest_date),
        )

        brands, lowest_price, lowest_brands = _format_brand_prices(cursor.fetchall())
        simple_prices = {
            brand_name: brand_data["lowest_price"]
            for brand_name, brand_data in brands.items()
            if brand_data["lowest_price"] is not None
        }

        return {
            "found": True,
            "query_city": city_name,
            "city": city["city_name"],
            "province": city["province_name"],
            "latest_date": latest_date,
            "prices": simple_prices,
            "brands": brands,
            "lowest_price": lowest_price,
            "lowest_brands": lowest_brands,
        }
    except Exception as exc:
        print(f"City fuel price lookup error: {exc}")
        return {
            "found": False,
            "message": "Unable to fetch city fuel prices.",
            "query_city": city_name,
            "brands": {},
            "lowest_price": None,
            "lowest_brands": [],
        }
    finally:
        conn.close()
