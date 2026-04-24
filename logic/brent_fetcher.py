import yfinance as yf
import json
import os
from datetime import datetime, timedelta

CACHE_FILE = 'data/brent_cache.json'

def get_live_brent_price():
    if not os.path.exists('data'):
        os.makedirs('data')
    
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
    
        last_updated = datetime.strptime(cache['timestamp'], '%Y-%m-%d %H:%M:%S')

        if datetime.now() - last_updated < timedelta(hours=24):
                print("Using Cached Data...")
                return round(cache['data'], 2)
    
    print("Fetching Fresh Data from Yahoo Finance...")

    try:
        ticker = yf.Ticker("BZ=F")
        current_price = ticker.fast_info['last_price']

        with open(CACHE_FILE, 'w') as f:
            json.dump({
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "data": current_price
            }, f)

        return round(current_price, 2)
    except Exception as e:
        print(f"Error fetching Brent price: {e}")
        return None