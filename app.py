from flask import Flask, render_template, url_for, request, jsonify
from logic.fuel_calculator import calculate_trip_cost
from logic.data_handler import get_fuel_history
from logic.brent_fetcher import get_live_brent_price
from logic.ave_FuelType_Data_query import get_ncr_fuel_averages
from logic.table_data_query import get_fuel_types, search_fuel_prices
app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def calculator():
    cost = None
    if request.method == 'POST':
        distance = float(request.form['distance'])
        efficiency = float(request.form['efficiency'])
        price = float(request.form['price'])
        cost = calculate_trip_cost(distance, efficiency, price)
    return render_template('calculator.html', title='Fuel Calculator', result=cost)

@app.route('/fuel_price')
def tracker():
    fuel_data = get_fuel_history()
    brent_price = get_live_brent_price()
    fuel_types = get_fuel_types()
    return render_template('fuel_price.html', data=fuel_data, brent_price=brent_price, fuel_types=fuel_types)

@app.route('/Nearest_GasStation')
def Nearest_GasStation():
    pass

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

if __name__ == '__main__':
    app.run(debug=True)
