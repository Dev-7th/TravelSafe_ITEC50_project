from flask import Flask, render_template, url_for, request
from logic.fuel_calculator import calculate_trip_cost
from logic.data_handler import get_fuel_history
from logic.brent_fetcher import get_live_brent_price
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
    return render_template('fuel_price.html', data=fuel_data, brent_price=brent_price)

@app.route('/Nearest_GasStation')
def Nearest_GasStation():
    pass
if __name__ == '__main__':
    app.run(debug=True)