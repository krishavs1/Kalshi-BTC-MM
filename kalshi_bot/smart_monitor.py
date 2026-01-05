import asyncio
import websockets
import json
import time
import requests
import csv
import os
from datetime import datetime, timezone, timedelta
from auth import get_auth_headers

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

def find_best_range_market(range_markets):
    """Select the range market with highest volume/volatility"""
    if not range_markets:
        return None
    
    scored = [calculate_volatility_score(m) for m in range_markets]
    scored.sort(key=lambda x: x['score'], reverse=True)
    
    best = scored[0]
    market = best['market']
    
    print(f"\n✅ Selected best range market:")
    print(f"   Ticker: {market.get('ticker')}")
    print(f"   Range: ${best['floor']:,.2f} - ${best['cap']:,.2f}")
    print(f"   Volume 24h: {best['volume_24h']}, Score: {best['score']:.2f}")
    
    return market

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

def get_market_orderbook(ticker):
    """Get current bid/ask for a market"""
    try:
        url = f"{REST_URL_BASE}/{ticker}"
        headers = get_auth_headers('GET', f'/trade-api/v2/markets/{ticker}')
        resp = requests.get(url, headers=headers, timeout=5)
        
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
    """Calculate 4 potential profit values"""
    if not (range_ob and lower_leg_ob and higher_leg_ob):
        return None
    
    # Extract values
    range_yes_bid = range_ob['yes_bid']
    range_yes_ask = range_ob['yes_ask']
    range_no_bid = range_ob['no_bid']
    range_no_ask = range_ob['no_ask']
    
    lower_yes_bid = lower_leg_ob['yes_bid']
    lower_yes_ask = lower_leg_ob['yes_ask']
    lower_no_bid = lower_leg_ob['no_bid']
    lower_no_ask = lower_leg_ob['no_ask']
    
    higher_yes_bid = higher_leg_ob['yes_bid']
    higher_yes_ask = higher_leg_ob['yes_ask']
    higher_no_bid = higher_leg_ob['no_bid']
    higher_no_ask = higher_leg_ob['no_ask']
    
    # Profit 1: Range YES overpriced
    # Range YES Bid − (Lower leg YES Ask + Higher leg NO Ask − 100)
    profit1 = range_yes_bid - (lower_yes_ask + higher_no_ask - 100)
    
    # Profit 2: Range YES underpriced
    # (Lower leg YES Bid + Higher leg NO Bid − 100) − Range YES Ask
    profit2 = (lower_yes_bid + higher_no_bid - 100) - range_yes_ask
    
    # Profit 3: Range NO overpriced
    # Range NO Bid − (Lower leg NO Ask + Higher leg YES Ask)
    profit3 = range_no_bid - (lower_no_ask + higher_yes_ask)
    
    # Profit 4: Range NO underpriced
    # (Lower leg NO Bid + Higher leg YES Bid) − Range NO Ask
    profit4 = (lower_no_bid + higher_yes_bid) - range_no_ask
    
    return {
        'profit1': profit1,
        'profit2': profit2,
        'profit3': profit3,
        'profit4': profit4
    }

def init_profit_csv(csv_filename):
    """Initialize CSV file with headers"""
    file_exists = os.path.exists(csv_filename)
    with open(csv_filename, 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Time', 'Profit 1 (Range YES overpriced)', 'Profit 2 (Range YES underpriced)', 
                           'Profit 3 (Range NO overpriced)', 'Profit 4 (Range NO underpriced)'])
    print(f"📝 Profit data will be logged to: {csv_filename}")

def log_profits_to_csv(csv_filename, profit1, profit2, profit3, profit4):
    """Append profit values to CSV file"""
    try:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(csv_filename, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([current_time, f"{profit1:.2f}", f"{profit2:.2f}", f"{profit3:.2f}", f"{profit4:.2f}"])
    except Exception as e:
        print(f"⚠️  Error writing to CSV: {e}")

async def monitor_markets(tickers, range_ticker, lower_leg_ticker, higher_leg_ticker, csv_filename=None):
    """Monitor multiple markets for order fulfillments"""
    print(f"\n🎯 Monitoring {len(tickers)} markets:")
    print(f"   Range: {range_ticker}")
    print(f"   Lower leg: {lower_leg_ticker}")
    print(f"   Higher leg: {higher_leg_ticker}")
    print("-" * 60)
    
    # Generate auth headers for WebSocket
    headers = get_auth_headers(method="GET", path="/trade-api/ws/v2")
    
    last_rest_poll = time.time()
    REST_POLL_INTERVAL = 1  # Poll every 1 second
    
    try:
        async with websockets.connect(WS_URL, extra_headers=headers) as websocket:
            print("✅ Connected to WebSocket!")
            
            # Subscribe to orderbook_delta for all tickers
            sub_msg = {
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": tickers
                }
            }
            await websocket.send(json.dumps(sub_msg))
            print(f"📤 Subscribed to orderbook_delta channel for {len(tickers)} markets")
            await asyncio.sleep(0.5)
            
            print("📡 Listening for order fulfillments...")
            print("   (Press Ctrl+C to stop)")
            print("-" * 60)
            
            await asyncio.sleep(1.0)
            
            while True:
                # Poll REST API periodically for trades and orderbook
                current_time = time.time()
                elapsed = current_time - last_rest_poll
                if elapsed >= REST_POLL_INTERVAL:
                    # Update poll time based on intended interval to prevent drift
                    # If we're late, advance by the interval, not by actual elapsed time
                    last_rest_poll = last_rest_poll + REST_POLL_INTERVAL
                    
                    # Get orderbooks for all markets
                    orderbooks = {}
                    for ticker in tickers:
                        orderbooks[ticker] = get_market_orderbook(ticker)
                    
                    # Print orderbook every second
                    print(f"\n📊 Orderbook Update - {datetime.now().strftime('%H:%M:%S')}")
                    for ticker in tickers:
                        ob = orderbooks.get(ticker)
                        if ob:
                            print(f"  {ticker}:")
                            print(f"    YES Bid: {ob['yes_bid']} | YES Ask: {ob['yes_ask']} | Last: {ob['last_price']}")
                            print(f"    NO Bid: {ob['no_bid']} | NO Ask: {ob['no_ask']}")
                    
                    # Calculate and display profits
                    range_ob = orderbooks.get(range_ticker)
                    lower_ob = orderbooks.get(lower_leg_ticker)
                    higher_ob = orderbooks.get(higher_leg_ticker)
                    
                    profits = calculate_profits(range_ob, lower_ob, higher_ob)
                    if profits:
                        print(f"\n💰 Profit Opportunities:")
                        print(f"  Profit 1 (Range YES overpriced): {profits['profit1']:.2f}")
                        print(f"  Profit 2 (Range YES underpriced): {profits['profit2']:.2f}")
                        print(f"  Profit 3 (Range NO overpriced): {profits['profit3']:.2f}")
                        print(f"  Profit 4 (Range NO underpriced): {profits['profit4']:.2f}")
                        
                        # Log to CSV
                        if csv_filename:
                            log_profits_to_csv(csv_filename, 
                                             profits['profit1'], 
                                             profits['profit2'], 
                                             profits['profit3'], 
                                             profits['profit4'])
                    
                    # Check for trades
                    for ticker in tickers:
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
        await monitor_markets(tickers)
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
    
    # 1. Get current EST time and determine target hour
    date_str, hour, year, month, day = get_current_est_hour()
    
    # 2. Find all range markets for that hour
    range_markets = find_range_markets(date_str)
    
    if not range_markets:
        print(f"❌ No range markets found for {date_str}")
        return
    
    # 3. Select best range market
    best_range = find_best_range_market(range_markets)
    if not best_range:
        print("❌ Could not select best range market")
        return
    
    range_ticker = best_range.get('ticker')
    
    # 4. Find corresponding 'over X' markets
    floor_over, cap_over = find_over_markets(best_range, date_str)
    
    # 5. Build list of markets to monitor
    tickers_to_monitor = [range_ticker]
    lower_leg_ticker = None
    higher_leg_ticker = None
    
    if floor_over:
        lower_leg_ticker = floor_over.get('ticker')
        tickers_to_monitor.append(lower_leg_ticker)
    if cap_over:
        higher_leg_ticker = cap_over.get('ticker')
        tickers_to_monitor.append(higher_leg_ticker)
    
    print(f"\n📊 Markets to monitor: {len(tickers_to_monitor)}")
    
    if not lower_leg_ticker or not higher_leg_ticker:
        print("❌ Missing leg markets - cannot calculate profits")
        return
    
    # 6. Initialize CSV logging
    csv_filename = f"profits_{date_str}.csv"
    init_profit_csv(csv_filename)
    
    # 7. Start monitoring
    await monitor_markets(tickers_to_monitor, range_ticker, lower_leg_ticker, higher_leg_ticker, csv_filename)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Exiting...")

