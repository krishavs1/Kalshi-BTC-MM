import requests
from datetime import datetime, timezone, timedelta
from auth import get_auth_headers

# Get current EST time and determine target hour
now_utc = datetime.now(timezone.utc)
est_offset = timedelta(hours=5)
now_est = now_utc - est_offset
current_hour = now_est.hour
next_hour = (current_hour + 1) % 24
target_day = now_est.day
if next_hour < current_hour:
    target_day = (now_est.day + 1) % 31
    if target_day == 0:
        target_day = 31

year = '26'
month = 'JAN'
day = f'{target_day:02d}'
hour = f'{next_hour:02d}'
date_str = f'{year}{month}{day}{hour}'

print(f'Current EST: {now_est.strftime("%Y-%m-%d %H:%M")}')
print(f'Looking for range markets expiring at: {next_hour:02d}:00 EST ({date_str})')
print('='*70)

# Find range markets
url = 'https://api.elections.kalshi.com/trade-api/v2/markets'
headers = get_auth_headers('GET', '/trade-api/v2/markets')
params = {'limit': 1000, 'event_ticker': f'KXBTC-{date_str}'}

resp = requests.get(url, headers=headers, params=params, timeout=10)
if resp.status_code == 200:
    markets = resp.json().get('markets', [])
    range_markets = [
        m for m in markets
        if date_str in m.get('ticker', '').upper()
        and m.get('ticker', '').startswith('KXBTC-')
        and '-B' in m.get('ticker', '')
        and m.get('market_type') == 'binary'
        and m.get('strike_type') == 'between'
    ]
    
    # Sort by volume_24h descending
    range_markets.sort(key=lambda x: x.get('volume_24h', 0) or 0, reverse=True)
    
    print(f'\nFound {len(range_markets)} range markets:\n')
    print(f'{"Ticker":<35} {"Volume 24h":<12} {"Volume":<12} {"Floor":<10} {"Cap":<10}')
    print('-'*70)
    
    for m in range_markets:
        ticker = m.get('ticker', '')
        vol_24h = m.get('volume_24h', 0) or 0
        volume = m.get('volume', 0) or 0
        floor = m.get('floor_strike', 0) or 0
        cap = m.get('cap_strike', 0) or 0
        print(f'{ticker:<35} {vol_24h:<12} {volume:<12} ${floor:,.0f}     ${cap:,.0f}')
else:
    print(f'Error: {resp.status_code} - {resp.text[:200]}')

