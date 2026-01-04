import requests
from auth import get_auth_headers

# Check what over X markets exist for 0414
url = 'https://api.elections.kalshi.com/trade-api/v2/markets'
headers = get_auth_headers('GET', '/trade-api/v2/markets')
params = {'limit': 2000, 'event_ticker': 'KXBTCD-26JAN0414'}

resp = requests.get(url, headers=headers, params=params, timeout=10)
if resp.status_code == 200:
    markets = resp.json().get('markets', [])
    over_markets = [m for m in markets if m.get('strike_type') == 'greater']
    
    print(f'Found {len(over_markets)} over X markets')
    
    # Look for ones around 91000 and 91249.99
    floor_target = 91000
    cap_target = 91249.99
    
    floor_matches = [m for m in over_markets if abs(m.get('floor_strike', 0) - floor_target) < 250]
    cap_matches = [m for m in over_markets if abs(m.get('floor_strike', 0) - cap_target) < 250]
    
    print(f'\nMarkets near floor ({floor_target}):')
    for m in sorted(floor_matches, key=lambda x: abs(x.get('floor_strike') - floor_target))[:5]:
        print(f'  {m.get("ticker")} - Over {m.get("floor_strike")}')
    
    print(f'\nMarkets near cap ({cap_target}):')
    for m in sorted(cap_matches, key=lambda x: abs(x.get('floor_strike') - cap_target))[:5]:
        print(f'  {m.get("ticker")} - Over {m.get("floor_strike")}')
        
    # Check the exact ones user mentioned
    print(f'\nChecking user examples:')
    user_lower = 'KXBTCD-26JAN0414-T90999.99'
    user_upper = 'KXBTCD-26JAN0414-T91249.99'
    
    for ticker in [user_lower, user_upper]:
        m = [m for m in over_markets if m.get('ticker') == ticker]
        if m:
            print(f'  ✅ {ticker} - Over {m[0].get("floor_strike")}')
        else:
            print(f'  ❌ {ticker} - NOT FOUND')
