import re
import sqlite3
from math import atan2, cos, radians, sin, sqrt

DB_NAME = "DataBase/fuel_prices.db"
BIG_BRANDS = ["PETRON", "SHELL", "CALTEX", "PHOENIX", "UNIOIL", "SEAOIL", "PTT", "FLYING V"]
PRICE_BRANDS = BIG_BRANDS + ["INDEPENDENT"]
EARTH_RADIUS_KM = 6371.0


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


def _format_brand_prices(rows, source="city", is_province_average=False):
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
                "source": source,
                "is_province_average": is_province_average,
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
                "source": source,
                "is_province_average": is_province_average,
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


def _merge_brand_price_summary(base_brands):
    lowest_price = None
    lowest_brands = set()
    simple_prices = {}

    for brand_name, brand_data in base_brands.items():
        brand_lowest = brand_data.get("lowest_price")
        if brand_lowest is None:
            continue

        simple_prices[brand_name] = brand_lowest
        if lowest_price is None or brand_lowest < lowest_price:
            lowest_price = brand_lowest
            lowest_brands = {brand_name}
        elif brand_lowest == lowest_price:
            lowest_brands.add(brand_name)

    return simple_prices, lowest_price, sorted(lowest_brands)


def _find_city(cursor, city_name):
    normalized_city = _normalize_location(city_name)
    if not normalized_city:
        return None

    cursor.execute(
        """
        SELECT c.id, c.name AS city_name, c.province_id, p.name AS province_name
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


def _haversine_km(lat1, lon1, lat2, lon2):
    """Return the great-circle distance in km between two latitude/longitude points."""
    lat1_rad = radians(float(lat1))
    lon1_rad = radians(float(lon1))
    lat2_rad = radians(float(lat2))
    lon2_rad = radians(float(lon2))

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def _build_city_fuel_price_response(cursor, city, query_city=None):
    placeholders = ",".join("?" for _ in PRICE_BRANDS)
    cursor.execute(
        f"""
        SELECT MAX(date_monitored)
        FROM price_records
        WHERE city_id = ?
          AND brand_name IN ({placeholders})
        """,
        (city["id"], *PRICE_BRANDS),
    )
    latest_date = cursor.fetchone()[0]

    brands = {}
    if latest_date:
        cursor.execute(
            f"""
            SELECT
                brand_name,
                fuel_type,
                ROUND(AVG(price_min), 2) AS price_min,
                ROUND(AVG(price_max), 2) AS price_max,
                date_monitored
            FROM price_records
            WHERE city_id = ?
              AND date_monitored = ?
              AND brand_name IN ({placeholders})
            GROUP BY brand_name, fuel_type, date_monitored
            ORDER BY brand_name, fuel_type
            """,
            (city["id"], latest_date, *PRICE_BRANDS),
        )
        brands, _, _ = _format_brand_prices(cursor.fetchall(), source="city")

    missing_brands = [brand_name for brand_name in PRICE_BRANDS if brand_name not in brands]
    if missing_brands:
        missing_placeholders = ",".join("?" for _ in missing_brands)
        cursor.execute(
            f"""
            SELECT
                pr.brand_name,
                pr.fuel_type,
                ROUND(AVG(pr.price_min), 2) AS price_min,
                ROUND(AVG(pr.price_max), 2) AS price_max,
                NULL AS date_monitored
            FROM price_records pr
            JOIN cities c ON pr.city_id = c.id
            WHERE c.province_id = ?
              AND pr.brand_name IN ({missing_placeholders})
            GROUP BY pr.brand_name, pr.fuel_type
            ORDER BY pr.brand_name, pr.fuel_type
            """,
            (city["province_id"], *missing_brands),
        )
        province_brands, _, _ = _format_brand_prices(
            cursor.fetchall(),
            source="province_average",
            is_province_average=True,
        )
        brands.update(province_brands)

    simple_prices, lowest_price, lowest_brands = _merge_brand_price_summary(brands)

    if not brands:
        return {
            "found": False,
            "message": f"No fuel price records found for '{city['city_name']}' or its province.",
            "query_city": query_city,
            "city": city["city_name"],
            "province": city["province_name"],
            "brands": {},
            "lowest_price": None,
            "lowest_brands": [],
        }

    return {
        "found": True,
        "query_city": query_city,
        "city": city["city_name"],
        "province": city["province_name"],
        "source_city": city["city_name"],
        "source_province": city["province_name"],
        "fallback_used": False,
        "latest_date": latest_date,
        "big_brands": BIG_BRANDS,
        "prices": simple_prices,
        "brands": brands,
        "lowest_price": lowest_price,
        "lowest_brands": lowest_brands,
    }


def _find_nearest_city_with_prices(cursor, latitude, longitude):
    placeholders = ",".join("?" for _ in PRICE_BRANDS)
    cursor.execute(
        f"""
        SELECT DISTINCT
            c.id,
            c.name AS city_name,
            c.province_id,
            c.latitude,
            c.longitude,
            p.name AS province_name
        FROM cities c
        JOIN provinces p ON c.province_id = p.id
        JOIN price_records pr ON pr.city_id = c.id
        WHERE c.latitude IS NOT NULL
          AND c.longitude IS NOT NULL
          AND pr.brand_name IN ({placeholders})
        """,
        PRICE_BRANDS,
    )

    nearest_city = None
    nearest_distance = None
    for city in cursor.fetchall():
        distance_km = _haversine_km(latitude, longitude, city["latitude"], city["longitude"])
        if nearest_distance is None or distance_km < nearest_distance:
            nearest_city = city
            nearest_distance = distance_km

    return nearest_city, nearest_distance


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

        return _build_city_fuel_price_response(cursor, city, query_city=city_name)
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


def get_prices_for_location(city_name, latitude, longitude):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        city = _find_city(cursor, city_name) if city_name else None
        if city:
            data = _build_city_fuel_price_response(cursor, city, query_city=city_name)
            data["requested_latitude"] = latitude
            data["requested_longitude"] = longitude
            return data

        nearest_city, distance_km = _find_nearest_city_with_prices(cursor, latitude, longitude)
        if not nearest_city:
            return {
                "found": False,
                "message": "No city with both coordinates and fuel price records was found.",
                "query_city": city_name,
                "brands": {},
                "lowest_price": None,
                "lowest_brands": [],
            }

        data = _build_city_fuel_price_response(cursor, nearest_city, query_city=city_name)
        if data.get("found"):
            data["fallback_used"] = True
            data["source_city"] = nearest_city["city_name"]
            data["source_province"] = nearest_city["province_name"]
            data["source_distance_km"] = round(distance_km, 2)
            data["requested_latitude"] = latitude
            data["requested_longitude"] = longitude
            data["message"] = (
                f"Prices for '{city_name or 'this location'}' are unavailable. "
                f"Showing prices from nearest city: {nearest_city['city_name']}."
            )
        return data
    except Exception as exc:
        print(f"Nearest fuel price lookup error: {exc}")
        return {
            "found": False,
            "message": "Unable to fetch nearest city fuel prices.",
            "query_city": city_name,
            "brands": {},
            "lowest_price": None,
            "lowest_brands": [],
        }
    finally:
        conn.close()
