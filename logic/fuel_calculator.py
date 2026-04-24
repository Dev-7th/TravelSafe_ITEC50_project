def calculate_trip_cost(distance, efficiency, price):
    if efficiency <= 0:
        return 0
    return round((distance / efficiency) * price, 2)