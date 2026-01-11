"""Trade detection and notification functions"""
from datetime import datetime
import requests
from auth import get_auth_headers
from config import REST_URL_BASE, last_price, last_volume, last_orderbook

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

def check_market_trades(ticker):
    """Check for trades on a specific market"""
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


