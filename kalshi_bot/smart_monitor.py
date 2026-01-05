import asyncio
import websockets
import json
import time
import requests
import csv
import os
import threading
import aiohttp
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from auth import get_auth_headers
try:
    from web_ui import update_data, run_server
    WEB_UI_AVAILABLE = True
except ImportError:
    WEB_UI_AVAILABLE = False
    print("⚠️  web_ui.py not available - web UI disabled")

# Configuration
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
REST_URL_BASE = "https://api.elections.kalshi.com/trade-api/v2/markets"

# Track recent trades to avoid duplicate notifications
seen_trades = {}
last_trade_time = {}
last_price = {}
last_volume = {}
last_orderbook = {}  # Track previous orderbook state

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

def determine_order_type(ticker, trade_info, prev_orderbook):
    """Determine if order was YES or NO based on which side of orderbook price is closer to"""
    curr_price = trade_info.get('price', 0)
    
    curr_yes_bid = trade_info.get('yes_bid', 0) or 0
    curr_yes_ask = trade_info.get('yes_ask', 100) or 100
    curr_no_bid = trade_info.get('no_bid', 0) or 0
    curr_no_ask = trade_info.get('no_ask', 100) or 100
    
    # Calculate minimum distance to YES side (use closest of bid or ask)
    if curr_yes_bid > 0 and curr_yes_ask < 100:
        dist_to_yes = min(abs(curr_price - curr_yes_bid), abs(curr_price - curr_yes_ask))
    elif curr_yes_bid > 0:
        dist_to_yes = abs(curr_price - curr_yes_bid)
    elif curr_yes_ask < 100:
        dist_to_yes = abs(curr_price - curr_yes_ask)
    else:
        dist_to_yes = float('inf')
    
    # Calculate minimum distance to NO side (convert NO prices to YES terms: 100 - NO_price)
    if curr_no_bid > 0 and curr_no_ask < 100:
        no_bid_yes_terms = 100 - curr_no_bid
        no_ask_yes_terms = 100 - curr_no_ask
        dist_to_no = min(abs(curr_price - no_bid_yes_terms), abs(curr_price - no_ask_yes_terms))
    elif curr_no_bid > 0:
        dist_to_no = abs(curr_price - (100 - curr_no_bid))
    elif curr_no_ask < 100:
        dist_to_no = abs(curr_price - (100 - curr_no_ask))
    else:
        dist_to_no = float('inf')
    
    # Whichever side is closer, that's the order type
    if dist_to_yes < dist_to_no:
        return "YES"
    elif dist_to_no < dist_to_yes:
        return "NO"
    else:
        # Equal distance (shouldn't happen often), default to YES
        return "YES"

def notify_order_fulfilled(ticker, trade_info):
    """Notify when an order is fulfilled"""
    global last_orderbook
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "="*60)
    print(f"🔔 ORDER FULFILLED - {ticker} - {timestamp}")
    print("="*60)
    if isinstance(trade_info, dict):
        prev_price = trade_info.get('previous_price')
        curr_price = trade_info.get('price', 0)
        price_dollars = trade_info.get('price_dollars', '0.0000')
        
        # Determine order type (YES or NO)
        prev_ob = last_orderbook.get(ticker, {})
        order_type = determine_order_type(ticker, trade_info, prev_ob)
        
        if prev_price is not None and prev_price != curr_price:
            price_change = curr_price - prev_price
            direction = "↑" if price_change > 0 else "↓"
            print(f"Order Type: {order_type}")
            print(f"Price: {curr_price} cents (${price_dollars}) {direction} {abs(price_change)} cents")
            print(f"Previous: {prev_price} cents")
        else:
            print(f"Order Type: {order_type}")
            print(f"Price: {curr_price} cents (${price_dollars})")
        
        volume = trade_info.get('volume', 0)
        volume_24h = trade_info.get('volume_24h', 0)
        print(f"Volume: {volume} (24h: {volume_24h})")
        
        print(f"\nCurrent Orderbook:")
        print(f"  YES Bid: {trade_info.get('yes_bid', 0)} | YES Ask: {trade_info.get('yes_ask', 0)}")
        print(f"  NO Bid: {trade_info.get('no_bid', 0)} | NO Ask: {trade_info.get('no_ask', 0)}")
    print("="*60 + "\n")

async def get_market_orderbook_async(session, ticker):
    """Get current bid/ask for a market (async version)"""
    try:
        url = f"{REST_URL_BASE}/{ticker}"
        headers = get_auth_headers('GET', f'/trade-api/v2/markets/{ticker}')
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=1.5)) as resp:
            if resp.status == 200:
                market_data = (await resp.json()).get('market', {})
                return {
                    'yes_bid': market_data.get('yes_bid', 0) or 0,
                    'yes_ask': market_data.get('yes_ask', 100) or 100,
                    'no_bid': market_data.get('no_bid', 0) or 0,
                    'no_ask': market_data.get('no_ask', 100) or 100,
                    'last_price': market_data.get('last_price', 0)
                }
    except:
        pass
    return None

def get_market_orderbook(ticker):
    """Get current bid/ask for a market (sync version for trade checking)"""
    try:
        url = f"{REST_URL_BASE}/{ticker}"
        headers = get_auth_headers('GET', f'/trade-api/v2/markets/{ticker}')
        resp = requests.get(url, headers=headers, timeout=1.5)
        
        if resp.status_code == 200:
            market_data = resp.json().get('market', {})
            return {
                'yes_bid': market_data.get('yes_bid', 0) or 0,
                'yes_ask': market_data.get('yes_ask', 100) or 100,
                'no_bid': market_data.get('no_bid', 0) or 0,
                'no_ask': market_data.get('no_ask', 100) or 100,
                'last_price': market_data.get('last_price', 0)
            }
    except:
        pass
    return None

def check_market_trades(ticker):
    """Check for trades on a specific market"""
    global last_price, last_volume
    
    try:
        url = f"{REST_URL_BASE}/{ticker}"
        headers = get_auth_headers('GET', f'/trade-api/v2/markets/{ticker}')
        resp = requests.get(url, headers=headers, timeout=5)
        
        if resp.status_code == 200:
            market_data = resp.json().get('market', {})
            current_price = market_data.get('last_price', 0)
            current_volume = market_data.get('volume', 0)
            current_volume_24h = market_data.get('volume_24h', 0)
            
            # Initialize tracking for this ticker
            if ticker not in last_price:
                last_price[ticker] = current_price
                last_volume[ticker] = current_volume
                if not hasattr(check_market_trades, '_last_vol24h'):
                    check_market_trades._last_vol24h = {}
                check_market_trades._last_vol24h[ticker] = current_volume_24h
                # Initialize orderbook tracking
                last_orderbook[ticker] = {
                    'yes_bid': market_data.get('yes_bid', 0),
                    'yes_ask': market_data.get('yes_ask', 100),
                    'no_bid': market_data.get('no_bid', 0),
                    'no_ask': market_data.get('no_ask', 100)
                }
                print(f"📊 Initialized tracking for {ticker}: Price={current_price}, Volume={current_volume}, Vol24h={current_volume_24h}")
                return
            
            # Check for changes
            if ticker not in check_market_trades._last_vol24h:
                check_market_trades._last_vol24h[ticker] = current_volume_24h
            
            volume_24h_changed = current_volume_24h != check_market_trades._last_vol24h[ticker]
            
            if current_price != last_price[ticker] or current_volume != last_volume[ticker] or volume_24h_changed:
                trade_info = {
                    "price": current_price,
                    "price_dollars": market_data.get('last_price_dollars', '0.0000'),
                    "previous_price": last_price[ticker],
                    "volume": current_volume,
                    "volume_24h": current_volume_24h,
                    "yes_bid": market_data.get('yes_bid', 0),
                    "yes_ask": market_data.get('yes_ask', 100),
                    "no_bid": market_data.get('no_bid', 0),
                    "no_ask": market_data.get('no_ask', 100),
                    "timestamp": datetime.now().isoformat()
                }
                notify_order_fulfilled(ticker, trade_info)
                
                # Update orderbook tracking after notification
                last_orderbook[ticker] = {
                    'yes_bid': market_data.get('yes_bid', 0),
                    'yes_ask': market_data.get('yes_ask', 100),
                    'no_bid': market_data.get('no_bid', 0),
                    'no_ask': market_data.get('no_ask', 100)
                }
            
            last_price[ticker] = current_price
            last_volume[ticker] = current_volume
            check_market_trades._last_vol24h[ticker] = current_volume_24h
            
    except Exception as e:
        pass  # Silently handle errors

def calculate_profits(range_ob, lower_leg_ob, higher_leg_ob):
    """Calculate 2 potential profit values for limit order strategy"""
    if not (range_ob and lower_leg_ob and higher_leg_ob):
        return None
    
    # Extract values
    range_yes_ask = range_ob['yes_ask']
    range_no_ask = range_ob['no_ask']
    
    lower_yes_ask = lower_leg_ob['yes_ask']
    lower_no_ask = lower_leg_ob['no_ask']
    
    higher_yes_ask = higher_leg_ob['yes_ask']
    higher_no_ask = higher_leg_ob['no_ask']
    
    # Profit 1: (Ask of Range YES − 1) − (Ask of Lower Leg YES) − (Ask of Higher Leg NO) + 100
    profit1 = (range_yes_ask - 1) - lower_yes_ask - higher_no_ask + 100
    
    # Profit 2: (Ask of Range NO − 1) − Ask of lower leg NO − Ask of higher leg YES + 100
    profit2 = (range_no_ask - 1) - lower_no_ask - higher_yes_ask + 100
    
    return {
        'profit1': profit1,
        'profit2': profit2
    }

def init_profit_csv(csv_filename, num_ranges=5):
    """Initialize CSV file with headers for multiple ranges"""
    file_exists = os.path.exists(csv_filename)
    with open(csv_filename, 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            headers = ['Time']
            for i in range(1, num_ranges + 1):
                headers.extend([
                    f'Range{i} Profit 1 (Range YES limit)',
                    f'Range{i} Profit 2 (Range NO limit)'
                ])
            writer.writerow(headers)
    print(f"📝 Profit data will be logged to: {csv_filename}")

def log_profits_to_csv(csv_filename, all_profits):
    """Append profit values for all ranges to CSV file
    
    Args:
        csv_filename: Path to CSV file
        all_profits: List of dicts, each containing profit1, profit2
    """
    try:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        row = [current_time]
        for profits in all_profits:
            if profits:
                row.extend([
                    f"{profits.get('profit1', 0):.2f}",
                    f"{profits.get('profit2', 0):.2f}"
                ])
            else:
                row.extend(['0.00', '0.00'])
        
        with open(csv_filename, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(row)
    except Exception as e:
        print(f"⚠️  Error writing to CSV: {e}")

async def find_and_setup_markets(init_csv=True):
    """Find and setup markets for current hour. Returns (market_sets, csv_filename, date_str)
    
    Args:
        init_csv: If True, initialize CSV file. If False, skip CSV initialization (for refreshes)
    """
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
    
    # Generate CSV filename
    csv_filename = f"profits_{date_str}.csv"
    if init_csv:
        init_profit_csv(csv_filename, num_ranges=len(market_sets))
    
    print(f"\n✅ Found {len(market_sets)} market sets for monitoring")
    for i, ms in enumerate(market_sets, 1):
        print(f"   Set {i}: Range={ms['range_ticker']}, Lower={ms['lower_leg_ticker']}, Higher={ms['higher_leg_ticker']}")
    
    return market_sets, csv_filename, date_str

async def monitor_markets(market_sets_ref, csv_filename_ref, date_str_ref):
    """Monitor markets with ability to update market_sets hourly
    
    Args:
        market_sets_ref: List that will be updated with new markets
        csv_filename_ref: List containing CSV filename (will be updated)
        date_str_ref: List containing date string (will be updated)
    """
    print(f"\n🎯 Monitoring {len(market_sets_ref)} range sets ({len(market_sets_ref) * 3} total markets):")
    for i, market_set in enumerate(market_sets_ref, 1):
        print(f"   Set {i}: Range={market_set['range_ticker']}, Lower={market_set['lower_leg_ticker']}, Higher={market_set['higher_leg_ticker']}")
    print("-" * 60)
    
    # Generate auth headers for WebSocket
    headers = get_auth_headers(method="GET", path="/trade-api/ws/v2")
    
    last_rest_poll = time.time()
    REST_POLL_INTERVAL = 0.5  # Poll every 0.5 seconds for faster updates
    last_market_refresh = time.time()
    MARKET_REFRESH_INTERVAL = 300  # Refresh market selection every 5 minutes (300 seconds)
    
    try:
        async with websockets.connect(WS_URL, additional_headers=headers) as websocket:
            print("✅ Connected to WebSocket!")
            
            # Subscribe to orderbook_delta for all tickers
            all_tickers_list = []
            for market_set in market_sets_ref:
                all_tickers_list.extend([
                    market_set['range_ticker'],
                    market_set['lower_leg_ticker'],
                    market_set['higher_leg_ticker']
                ])
            all_tickers_list = list(set(all_tickers_list))  # Remove duplicates
            
            sub_msg = {
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": all_tickers_list
                }
            }
            await websocket.send(json.dumps(sub_msg))
            print(f"📤 Subscribed to orderbook_delta channel for {len(all_tickers_list)} markets")
            await asyncio.sleep(0.5)
            
            print("📡 Listening for order fulfillments...")
            print("   (Press Ctrl+C to stop)")
            print("-" * 60)
            
            await asyncio.sleep(1.0)
            
            while True:
                # Check if we need to refresh markets (every 5 minutes)
                current_time = time.time()
                if current_time - last_market_refresh >= MARKET_REFRESH_INTERVAL:
                    last_market_refresh = current_time
                    print(f"\n⏰ 5 minutes elapsed - Refreshing market selection...")
                    new_market_sets, new_csv_filename, new_date_str = await find_and_setup_markets(init_csv=False)
                    if new_market_sets:
                        # Update the references (but keep same CSV filename if same hour)
                        market_sets_ref.clear()
                        market_sets_ref.extend(new_market_sets)
                        # Only update CSV filename if hour changed, otherwise keep same file
                        if new_date_str != date_str_ref[0]:
                            csv_filename_ref[0] = new_csv_filename
                            date_str_ref[0] = new_date_str
                        else:
                            # Same hour, keep using existing CSV file
                            print(f"   ℹ️  Keeping existing CSV file: {csv_filename_ref[0]}")
                        
                        # Re-subscribe to WebSocket for new markets
                        all_tickers_list = []
                        for market_set in market_sets_ref:
                            all_tickers_list.extend([
                                market_set['range_ticker'],
                                market_set['lower_leg_ticker'],
                                market_set['higher_leg_ticker']
                            ])
                        all_tickers_list = list(set(all_tickers_list))
                        
                        sub_msg = {
                            "id": 2,
                            "cmd": "subscribe",
                            "params": {
                                "channels": ["orderbook_delta"],
                                "market_tickers": all_tickers_list
                            }
                        }
                        await websocket.send(json.dumps(sub_msg))
                        print(f"📤 Re-subscribed to {len(all_tickers_list)} markets")
                
                # Poll REST API periodically for trades and orderbook
                elapsed = current_time - last_rest_poll
                if elapsed >= REST_POLL_INTERVAL:
                    # Update poll time based on intended interval to prevent drift
                    last_rest_poll = last_rest_poll + REST_POLL_INTERVAL
                    
                    # Use current market_sets (may have been updated)
                    current_market_sets = list(market_sets_ref)
                    
                    # Collect all unique tickers
                    all_tickers = []
                    for market_set in current_market_sets:
                        all_tickers.extend([
                            market_set['range_ticker'],
                            market_set['lower_leg_ticker'],
                            market_set['higher_leg_ticker']
                        ])
                    all_tickers = list(set(all_tickers))  # Remove duplicates
                    
                    # Get orderbooks for all markets concurrently using async
                    orderbooks = {}
                    async with aiohttp.ClientSession() as session:
                        tasks = [get_market_orderbook_async(session, ticker) for ticker in all_tickers]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        for ticker, result in zip(all_tickers, results):
                            if isinstance(result, Exception) or result is None:
                                orderbooks[ticker] = None
                            else:
                                orderbooks[ticker] = result
                    
                    # Calculate profits for all sets
                    all_profits = []
                    
                    for i, market_set in enumerate(current_market_sets):
                        range_ob = orderbooks.get(market_set['range_ticker'])
                        lower_ob = orderbooks.get(market_set['lower_leg_ticker'])
                        higher_ob = orderbooks.get(market_set['higher_leg_ticker'])
                        
                        profits = calculate_profits(range_ob, lower_ob, higher_ob)
                        all_profits.append(profits)
                    
                    # Update web UI with all sets data
                    if WEB_UI_AVAILABLE:
                        try:
                            all_sets_ui_data = []
                            for i, market_set in enumerate(current_market_sets):
                                range_ob = orderbooks.get(market_set['range_ticker'])
                                lower_ob = orderbooks.get(market_set['lower_leg_ticker'])
                                higher_ob = orderbooks.get(market_set['higher_leg_ticker'])
                                profits = all_profits[i] if i < len(all_profits) else None
                                
                                all_sets_ui_data.append({
                                    'orderbooks': {
                                        'range': range_ob,
                                        'lower': lower_ob,
                                        'higher': higher_ob
                                    },
                                    'profits': profits or {},
                                    'tickers': {
                                        'range': market_set['range_ticker'],
                                        'lower': market_set['lower_leg_ticker'],
                                        'higher': market_set['higher_leg_ticker']
                                    }
                                })
                            update_data(all_sets_ui_data)
                        except:
                            pass  # Silently fail if UI not ready
                    
                    # Log all profits to CSV
                    if csv_filename_ref[0]:
                        log_profits_to_csv(csv_filename_ref[0], all_profits)
                    
                    # Check for trades
                    for ticker in all_tickers:
                        check_market_trades(ticker)
                
                # Check for WebSocket messages with timeout
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                # Handle ping frames
                if isinstance(message, bytes):
                    continue
                
                try:
                    data = json.loads(message)
                    if not isinstance(data, dict):
                        continue
                    
                    msg_type = data.get("type") or data.get("cmd") or "unknown"
                    
                    # Handle orderbook delta silently (just keep connection alive)
                    if msg_type == "orderbook_delta":
                        pass
                    elif msg_type == "error":
                        error_msg = data.get('msg', {})
                        if isinstance(error_msg, dict):
                            error_code = error_msg.get('code', 'unknown')
                            if error_code != 8:  # Skip "Unknown channel" errors
                                print(f"❌ Error: {error_msg.get('msg', error_msg)}")
                    
                except:
                    continue
                    
    except websockets.exceptions.ConnectionClosed:
        print("❌ Connection lost. Attempting to reconnect...")
        await asyncio.sleep(5)
        await monitor_markets(market_sets_ref, csv_filename_ref, date_str_ref)
    except KeyboardInterrupt:
        print("\n\n👋 Stopped monitoring.")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

async def main():
    """Main function"""
    print("="*60)
    print("🎯 Smart Market Monitor")
    print("="*60)
    
    # Initialize web UI if available
    if WEB_UI_AVAILABLE:
        try:
            # Start web server in background thread (use 5001 to avoid macOS AirPlay conflict on 5000)
            ui_thread = threading.Thread(target=run_server, daemon=True, args=('127.0.0.1', 5001, False))
            ui_thread.start()
            print("🌐 Web UI started at http://127.0.0.1:5001")
            time.sleep(1)  # Give server a moment to start
        except Exception as e:
            print(f"⚠️  Could not start web UI: {e}")
    
    # Find initial markets
    market_sets, csv_filename, date_str = await find_and_setup_markets()
    if not market_sets:
        print("❌ Failed to initialize markets")
        return
    
    # Use lists/dicts as references so monitor_markets can update them
    market_sets_ref = market_sets  # Already a list, will be updated in place
    csv_filename_ref = [csv_filename]  # Wrap in list for reference update
    date_str_ref = [date_str]  # Wrap in list for reference update
    
    # Start monitoring (will refresh markets every hour)
    await monitor_markets(market_sets_ref, csv_filename_ref, date_str_ref)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Exiting...")

