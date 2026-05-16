import argparse
import json
import sqlite3
import time
import urllib.parse
import urllib.request

DB_NAME = "DataBase/fuel_prices.db"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "TravelSafeFuelPriceTracker/1.0"


def fetch_coordinates(city_name, province_name):
    queries = [
        f"{city_name}, {province_name}, Philippines",
        f"{city_name}, Philippines",
    ]

    for query in queries:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "limit": 1,
        })
        request = urllib.request.Request(
            f"{NOMINATIM_URL}?{params}",
            headers={"User-Agent": USER_AGENT},
        )

        with urllib.request.urlopen(request, timeout=20) as response:
            results = json.loads(response.read().decode("utf-8"))

        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])

    return None, None


def update_missing_city_coordinates(limit=None, delay_seconds=1.0):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT c.id, c.name, p.name
        FROM cities c
        LEFT JOIN provinces p ON c.province_id = p.id
        WHERE c.latitude IS NULL
           OR c.longitude IS NULL
        ORDER BY c.id
        """
    )
    rows = cursor.fetchall()
    if limit:
        rows = rows[:limit]

    updated = 0
    skipped = 0
    for city_id, city_name, province_name in rows:
        try:
            latitude, longitude = fetch_coordinates(city_name, province_name or "")
            if latitude is None or longitude is None:
                skipped += 1
                print(f"Skipped: {city_name} ({province_name}) - no coordinates found")
            else:
                cursor.execute(
                    """
                    UPDATE cities
                    SET latitude = ?, longitude = ?
                    WHERE id = ?
                    """,
                    (latitude, longitude, city_id),
                )
                conn.commit()
                updated += 1
                print(f"Updated: {city_name} ({province_name}) -> {latitude}, {longitude}")
        except Exception as exc:
            skipped += 1
            print(f"Error: {city_name} ({province_name}) -> {exc}")

        time.sleep(delay_seconds)

    conn.close()
    print(f"\nDone. Updated {updated} cities. Skipped {skipped} cities.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch latitude/longitude for cities in DataBase/fuel_prices.db."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only update the first N missing city coordinates.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between geocoding requests.",
    )
    args = parser.parse_args()

    update_missing_city_coordinates(limit=args.limit, delay_seconds=args.delay)
