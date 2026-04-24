import pandas as pd

def get_fuel_history():
    df = pd.read_csv('data_set/crude_oil_daily.csv')
    fuel_price_data = df[['Date', 'Brent_USD', 'WTI_USD']]
    fuel_price_data = fuel_price_data.sort_values(by='Date', ascending=False)
    return fuel_price_data.to_dict(orient='records')