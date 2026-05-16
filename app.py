from flask import Flask, render_template, url_for, request, jsonify
from logic.fuel_calculator import (
    calculate_liters_needed,
    calculate_trip_cost,
    get_latest_average_fuel_price,
    get_vehicle,
    get_vehicle_groups,
)
from logic.data_handler import get_fuel_history
from logic.brent_fetcher import get_live_brent_price
from logic.ave_FuelType_Data_query import get_ncr_fuel_averages
from logic.table_data_query import get_fuel_types, search_fuel_prices
from logic.data_fetcher_DB import get_latest_city_fuel_prices, get_prices_for_location
app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def calculator():
    result = None
    error = None
    selected_vehicle = ''
    selected_fuel_type = ''
    distance_value = ''
    roundtrip = False
    fuel_types = get_fuel_types()

    if request.method == 'POST':
        selected_vehicle = request.form.get('vehicle_type', '').strip()
        selected_fuel_type = request.form.get('fuel_type', '').strip()
        distance_value = request.form.get('distance', '').strip()
        roundtrip = request.form.get('roundtrip') == 'on'

        try:
            distance = float(distance_value)
        except ValueError:
            distance = 0

        vehicle = get_vehicle(selected_vehicle)
        price_per_liter = get_latest_average_fuel_price(selected_fuel_type) if selected_fuel_type else None

        if not vehicle:
            error = 'Please select a vehicle type.'
        elif distance <= 0:
            error = 'Please enter a valid distance.'
        elif price_per_liter is None:
            error = 'No latest fuel price was found for the selected fuel type.'
        else:
            total_distance = distance * 2 if roundtrip else distance
            liters_needed = calculate_liters_needed(total_distance, vehicle['efficiency'])
            total_cost = calculate_trip_cost(total_distance, vehicle['efficiency'], price_per_liter)
            result = {
                'vehicle': vehicle,
                'fuel_type': selected_fuel_type,
                'one_way_distance': distance,
                'total_distance': total_distance,
                'roundtrip': roundtrip,
                'price_per_liter': price_per_liter,
                'liters_needed': liters_needed,
                'total_cost': total_cost,
            }

    return render_template(
        'calculator.html',
        title='Fuel Calculator',
        result=result,
        error=error,
        vehicle_groups=get_vehicle_groups(),
        fuel_types=fuel_types,
        selected_vehicle=selected_vehicle,
        selected_fuel_type=selected_fuel_type,
        distance_value=distance_value,
        roundtrip=roundtrip,
    )

@app.route('/fuel_price')
def tracker():
    fuel_data = get_fuel_history()
    brent_price = get_live_brent_price()
    fuel_types = get_fuel_types()
    return render_template('fuel_price.html', data=fuel_data, brent_price=brent_price, fuel_types=fuel_types)

@app.route('/map')
def map():
    return render_template('map.html')

@app.route('/api/ncr-averages')
def ncr_averages_api():
    data = get_ncr_fuel_averages()
    
    return jsonify(data)

@app.route('/api/fuel-prices')
def fuel_prices_api():
    search_term = request.args.get('q', '')
    fuel_type = request.args.get('fuel_type', '')
    data = search_fuel_prices(search_term, fuel_type)

    return jsonify(data)

@app.route('/api/city-fuel-prices')
def city_fuel_prices_api():
    city = request.args.get('city', '').strip()
    if not city:
        return jsonify({
            'found': False,
            'message': 'City is required.',
            'brands': {},
            'lowest_price': None,
            'lowest_brands': []
        }), 400

    data = get_latest_city_fuel_prices(city)
    status_code = 200 if data.get('found') else 404
    return jsonify(data), status_code

@app.route('/api/get-nearest-prices')
def nearest_fuel_prices_api():
    city = request.args.get('city', '').strip()
    lat = request.args.get('lat', '').strip()
    lng = request.args.get('lng', '').strip()

    if not lat or not lng:
        return jsonify({
            'found': False,
            'message': 'Latitude and longitude are required.',
            'brands': {},
            'lowest_price': None,
            'lowest_brands': []
        }), 400

    try:
        latitude = float(lat)
        longitude = float(lng)
    except ValueError:
        return jsonify({
            'found': False,
            'message': 'Latitude and longitude must be valid numbers.',
            'brands': {},
            'lowest_price': None,
            'lowest_brands': []
        }), 400

    data = get_prices_for_location(city, latitude, longitude)
    status_code = 200 if data.get('found') else 404
    return jsonify(data), status_code

if __name__ == '__main__':
    app.run(debug=True)
