import sqlite3

DB_NAME = "DataBase/fuel_prices.db"

VEHICLE_GROUPS = [
    {
        "name": "Personal Vehicles",
        "vehicles": [
            {
                "key": "mini_hatchback",
                "label": "Mini Hatchback (1.0L)",
                "examples": "Wigo, S-Presso, Celerio",
                "efficiency": 18,
            },
            {
                "key": "subcompact",
                "label": "Subcompact (1.3L - 1.5L)",
                "examples": "Vios, City, Mirage G4, Almera",
                "efficiency": 14,
            },
            {
                "key": "compact_executive_sedan",
                "label": "Compact/Executive Sedan",
                "examples": "Civic, Corolla Altis, Camry",
                "efficiency": 10,
            },
            {
                "key": "hybrid_vehicle",
                "label": "Hybrid Vehicle (Gas + Electric)",
                "examples": "Corolla Cross Hybrid, Yaris Cross",
                "efficiency": 22,
            },
            {
                "key": "small_crossover",
                "label": "Small Crossover",
                "examples": "Raize, HR-V, Territory, Stonic",
                "efficiency": 12,
            },
            {
                "key": "midsize_suv_diesel",
                "label": "Mid-Size SUV (Diesel)",
                "examples": "Fortuner, Montero Sport, Terra",
                "efficiency": 10,
            },
            {
                "key": "midsize_suv_mpv_gas",
                "label": "Mid-Size SUV/MPV (Gas)",
                "examples": "Innova (Gas), Older SUVs",
                "efficiency": 8,
            },
            {
                "key": "fullsize_suv_luxury_van",
                "label": "Full-Size SUV / Luxury Van",
                "examples": "Land Cruiser, Alphard, Carnival",
                "efficiency": 6.5,
            },
            {
                "key": "pickup_truck",
                "label": "Pickup Truck",
                "examples": "Hilux, Ranger, D-Max, Navara",
                "efficiency": 9,
            },
        ],
    },
    {
        "name": "Motorcycles",
        "vehicles": [
            {
                "key": "motorcycle_commuter",
                "label": "Motorcycle (Commuter)",
                "examples": "NMAX, Click, Beat, Aerox",
                "efficiency": 45,
            },
            {
                "key": "big_bike",
                "label": "Big Bike (400cc+)",
                "examples": "Ninja 400, Rebel, Dominar",
                "efficiency": 20,
            },
        ],
    },
    {
        "name": "Public Transport & Logistics",
        "vehicles": [
            {
                "key": "traditional_jeepney",
                "label": "Traditional Jeepney",
                "examples": "Sarao, Owner-type",
                "efficiency": 7,
            },
            {
                "key": "modern_jeepney_minibus",
                "label": "Modern Jeepney / Mini-Bus",
                "examples": "Gazelle, Solar, Hino",
                "efficiency": 9,
            },
            {
                "key": "light_cargo_truck",
                "label": "Light Cargo Truck (6-Wheeler)",
                "examples": "Isuzu Elf, Fuso Canter",
                "efficiency": 6,
            },
            {
                "key": "heavy_cargo_truck",
                "label": "Heavy Cargo Truck (10-Wheeler)",
                "examples": "Wing Van, Dump Truck, Trailer",
                "efficiency": 3,
            },
        ],
    },
]

VEHICLES_BY_KEY = {
    vehicle["key"]: vehicle
    for group in VEHICLE_GROUPS
    for vehicle in group["vehicles"]
}

def get_vehicle_groups():
    return VEHICLE_GROUPS

def get_vehicle(vehicle_key):
    return VEHICLES_BY_KEY.get(vehicle_key)

def get_latest_average_fuel_price(fuel_type):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            WITH latest_report AS (
                SELECT MAX(date_monitored) AS date_monitored
                FROM price_records
                WHERE date_monitored IS NOT NULL
            )
            SELECT ROUND(AVG((price_min + price_max) / 2), 2)
            FROM price_records pr
            JOIN latest_report lr ON pr.date_monitored = lr.date_monitored
            WHERE pr.fuel_type = ?
                AND pr.brand_name != 'OVERALL RANGE'
                AND pr.price_min IS NOT NULL
                AND pr.price_max IS NOT NULL
            """,
            (fuel_type,),
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None
    finally:
        conn.close()

def calculate_liters_needed(distance, efficiency):
    if distance <= 0 or efficiency <= 0:
        return 0
    return round(distance / efficiency, 2)

def calculate_trip_cost(distance, efficiency, price):
    if distance <= 0 or efficiency <= 0 or price <= 0:
        return 0
    return round((distance / efficiency) * price, 2)
