"""Market discovery and selection functions"""
import requests
from datetime import datetime, timezone, timedelta
from auth import get_auth_headers
from config import REST_URL_BASE

def get_current_est_hour():
    """Get current EST time and determine the next hour for market expiration"""
    # EST is UTC-5, EDT is UTC-4 (we'll use EST for simplicity)
    now_utc = datetime.now(timezone.utc)
    est_offset = timedelta(hours=5)  # EST is UTC-5
    now_est = now_utc - est_offset
    
    # Get next hour (if it's 1:21 PM, we want 2 PM markets, so hour 14)
    current_hour = now_est.hour
    next_hour = (current_hour + 1) % 24
    
    # Handle day rollover
    target_day = now_est.day
    if next_hour < current_hour:  # Rolled over to next day
        target_day = (now_est.day + 1) % 31
        if target_day == 0:
            target_day = 31  # Simple handling, adjust if needed
    
    # Format: 26JAN{DAY}{HOUR}
    # Year: 26 (2026), Month: JAN, Day: DD, Hour: HH (24-hour format, EST)
    year = "26"
    month = "JAN"
    day = f"{target_day:02d}"
    hour = f"{next_hour:02d}"
    
    date_str = f"{year}{month}{day}{hour}"
    
    print(f"🕐 Current EST: {now_est.strftime('%Y-%m-%d %H:%M')}")
    print(f"📅 Looking for markets expiring at: {next_hour:02d}:00 EST ({date_str})")
    
    return date_str, next_hour, now_est.year, now_est.month, target_day

def find_range_markets(date_str):
    """Find all range markets for the given expiration time"""
    print(f"\n🔍 Searching for range markets with pattern: KXBTC-{date_str}-B*")
    
    url = f"{REST_URL_BASE}"
    headers = get_auth_headers('GET', '/trade-api/v2/markets')
    
    # First try searching by event_ticker (more reliable)
    event_ticker = f'KXBTC-{date_str}'
    params_list = [
        {'limit': 1000, 'event_ticker': event_ticker},
        {'limit': 1000, 'series_ticker': 'KXBT'},
        {'limit': 2000, 'series_ticker': 'KXBTC'},
    ]
    
    all_markets = []
    for params in params_list:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                markets = resp.json().get('markets', [])
                # Filter for range markets matching the date pattern
                matching = [
                    m for m in markets
                    if date_str in m.get('ticker', '').upper() 
                    and m.get('ticker', '').startswith('KXBTC-')
                    and '-B' in m.get('ticker', '')
                    and m.get('market_type') == 'binary'
                    and m.get('strike_type') == 'between'
                ]
                if matching:
                    all_markets = matching
                    print(f"   Found using params: {params}")
                    break
        except:
            continue
    
    print(f"✅ Found {len(all_markets)} range markets")
    return all_markets

def calculate_volatility_score(market):
    """Calculate volatility/activity score for a market"""
    floor = market.get('floor_strike', 0)
    cap = market.get('cap_strike', 0)
    
    # Bid-ask spread (tighter = more liquid/active)
    yes_bid = market.get('yes_bid', 0) or 0
    yes_ask = market.get('yes_ask', 0) or 100
    no_bid = market.get('no_bid', 0) or 0
    no_ask = market.get('no_ask', 0) or 100
    
    yes_spread = yes_ask - yes_bid if yes_ask > yes_bid else 100
    no_spread = no_ask - no_bid if no_ask > no_bid else 100
    bid_ask_spread = min(yes_spread, no_spread)
    
    # Volume indicators
    volume_24h = market.get('volume_24h', 0) or 0
    volume = market.get('volume', 0) or 0
    notional_value = float(market.get('notional_value_dollars', '0') or 0)
    open_interest = market.get('open_interest', 0) or 0
    
    # Liquidity score
    liquidity_score = 1 + (notional_value / 100) + (open_interest / 1000) + (volume_24h / 10000)
    
    # Volatility score: inverse spread * liquidity
    if bid_ask_spread >= 100:
        base_score = liquidity_score * 10
    else:
        base_score = (100 - bid_ask_spread) * liquidity_score
    
    return {
        'market': market,
        'score': base_score,
        'volume_24h': volume_24h,
        'volume': volume,
        'spread': bid_ask_spread,
        'notional': notional_value,
        'floor': floor,
        'cap': cap
    }

def find_top_range_markets(range_markets, top_n=5):
    """Select the top N range markets with highest volume/volatility"""
    if not range_markets:
        return []
    
    scored = [calculate_volatility_score(m) for m in range_markets]
    scored.sort(key=lambda x: x['score'], reverse=True)
    
    top_markets = []
    for i, best in enumerate(scored[:top_n]):
        market = best['market']
        top_markets.append(market)
        print(f"\n✅ Top {i+1} range market:")
        print(f"   Ticker: {market.get('ticker')}")
        print(f"   Range: ${best['floor']:,.2f} - ${best['cap']:,.2f}")
        print(f"   Volume 24h: {best['volume_24h']}, Score: {best['score']:.2f}")
    
    return top_markets

def find_over_markets(range_market, date_str):
    """Find the corresponding 'over X' markets for the range bounds"""
    floor = range_market.get('floor_strike')
    cap = range_market.get('cap_strike')
    
    if not floor or not cap:
        return None, None
    
    print(f"\n🔍 Finding 'over X' markets for range ${floor:,.2f} - ${cap:,.2f}")
    
    url = f"{REST_URL_BASE}"
    headers = get_auth_headers('GET', '/trade-api/v2/markets')
    
    # Search for KXBTCD markets with same expiration
    params = {'limit': 1000, 'event_ticker': f'KXBTCD-{date_str}'}
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            markets = resp.json().get('markets', [])
            
            # Find 'over X' markets matching the floor and cap strikes
            # Lower over: typically floor - 0.01 (e.g., floor=91000 -> over 90999.99)
            # Upper over: typically cap exactly (e.g., cap=91249.99 -> over 91249.99)
            floor_over = None
            cap_over = None
            
            greater_markets = [
                m for m in markets
                if m.get('strike_type') == 'greater' and m.get('market_type') == 'binary'
                and m.get('floor_strike') is not None
            ]
            
            if not greater_markets:
                print(f"   ⚠️  No 'over X' markets found for KXBTCD-{date_str}")
                return None, None
            
            # Lower over: look for floor - 0.01 or closest to floor
            lower_target = floor - 0.01
            floor_over = min(greater_markets, 
                           key=lambda x: abs(x.get('floor_strike', 999999) - lower_target))
            floor_diff = abs(floor_over.get('floor_strike') - lower_target)
            # Verify it's close enough (within 250)
            if floor_diff > 250:
                print(f"   ⚠️  Closest lower market is too far: diff=${floor_diff:.2f}")
                floor_over = None
            
            # Upper over: look for cap exactly or closest
            cap_over = min(greater_markets,
                         key=lambda x: abs(x.get('floor_strike', 999999) - cap))
            cap_diff = abs(cap_over.get('floor_strike') - cap)
            # Verify it's close enough (within 1, since it should match exactly)
            if cap_diff > 1.0:
                print(f"   ⚠️  Closest upper market is too far: diff=${cap_diff:.2f}")
                cap_over = None
            
            if floor_over:
                print(f"   ✅ Lower bound: {floor_over.get('ticker')} (Over ${floor_over.get('floor_strike'):,.2f})")
            else:
                print(f"   ⚠️  Could not find lower bound market for ${floor:,.2f}")
            
            if cap_over:
                print(f"   ✅ Upper bound: {cap_over.get('ticker')} (Over ${cap_over.get('floor_strike'):,.2f})")
            else:
                print(f"   ⚠️  Could not find upper bound market for ${cap:,.2f}")
            
            return floor_over, cap_over
        else:
            print(f"   ❌ API Error: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"   ❌ Error finding over markets: {e}")
        import traceback
        traceback.print_exc()
    
    return None, None

async def find_and_setup_markets(init_csv=True):
    """Find and setup markets for current hour. Returns (market_sets, csv_filename, date_str)
    
    Args:
        init_csv: If True, initialize CSV file. If False, skip CSV initialization (for refreshes)
    """
    from profit_calculator import init_profit_csv
    
    # Get current EST hour
    date_str, est_hour, _, _, _ = get_current_est_hour()
    
    print(f"\n🔍 Finding range markets for {date_str} at hour {est_hour}...")
    
    # Find all range markets for this hour
    range_markets = find_range_markets(date_str)
    if not range_markets:
        print(f"❌ No range markets found for {date_str}")
        return None, None, date_str
    
    # Select top 5 range markets
    top_ranges = find_top_range_markets(range_markets, top_n=5)
    if not top_ranges:
        print(f"❌ No valid range markets found")
        return None, None, date_str
    
    # Find over/under leg markets for each range
    market_sets = []
    for range_market in top_ranges:
        lower_leg, higher_leg = find_over_markets(range_market, date_str)
        if lower_leg and higher_leg:
            market_sets.append({
                'range_ticker': range_market.get('ticker'),
                'lower_leg_ticker': lower_leg.get('ticker'),
                'higher_leg_ticker': higher_leg.get('ticker')
            })
    
    if not market_sets:
        print(f"❌ Could not find leg markets for any ranges")
        return None, None, date_str
    
    # Generate CSV filename in data folder
    import os
    os.makedirs('data', exist_ok=True)
    csv_filename = f"data/profits_{date_str}.csv"
    if init_csv:
        init_profit_csv(csv_filename, num_ranges=len(market_sets))
    
    print(f"\n✅ Found {len(market_sets)} market sets for monitoring")
    for i, ms in enumerate(market_sets, 1):
        print(f"   Set {i}: Range={ms['range_ticker']}, Lower={ms['lower_leg_ticker']}, Higher={ms['higher_leg_ticker']}")
    
    return market_sets, csv_filename, date_str

