import asyncio
import websockets
import json
import time
import requests
from datetime import datetime
from auth import get_auth_headers

# Configuration
MARKET_TICKER = "KXBTCD-26JAN0323-T91249.99"
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
REST_URL = f"https://api.elections.kalshi.com/trade-api/v2/markets/{MARKET_TICKER}"

# Track recent trades to avoid duplicate notifications
seen_trades = set()
last_trade_time = None
last_price = None
last_volume = None

def notify_order_fulfilled(trade_info):
    """Notify when an order is fulfilled"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "="*60)
    print(f"🔔 ORDER FULFILLED - {timestamp}")
    print("="*60)
    if isinstance(trade_info, dict):
        print(f"Market: {trade_info.get('market_ticker', MARKET_TICKER)}")
        
        prev_price = trade_info.get('previous_price')
        curr_price = trade_info.get('price', 0)
        price_dollars = trade_info.get('price_dollars', '0.0000')
        
        if prev_price is not None and prev_price != curr_price:
            price_change = curr_price - prev_price
            direction = "↑" if price_change > 0 else "↓"
            print(f"Price: {curr_price} cents (${price_dollars}) {direction} {abs(price_change)} cents")
            print(f"Previous: {prev_price} cents")
        else:
            print(f"Price: {curr_price} cents (${price_dollars})")
        
        volume = trade_info.get('volume', 0)
        volume_24h = trade_info.get('volume_24h', 0)
        print(f"Volume: {volume} (24h: {volume_24h})")
        
        print(f"\nCurrent Orderbook:")
        print(f"  YES Bid: {trade_info.get('yes_bid', 0)} | YES Ask: {trade_info.get('yes_ask', 0)}")
        print(f"  NO Bid: {trade_info.get('no_bid', 0)} | NO Ask: {trade_info.get('no_ask', 0)}")
    else:
        print(f"Trade: {trade_info}")
    print("="*60 + "\n")
    
    # You can extend this to send email, SMS, Slack, etc.
    # Example: send_email_notification(trade_info)

def check_recent_trades():
    """Poll REST API to check for recent trades by tracking price/volume changes"""
    global last_trade_time, last_price, last_volume
    
    try:
        headers = get_auth_headers('GET', f'/trade-api/v2/markets/{MARKET_TICKER}')
        resp = requests.get(REST_URL, headers=headers, timeout=5)
        
        if resp.status_code == 200:
            market_data = resp.json().get('market', {})
            current_price = market_data.get('last_price', 0)
            current_volume = market_data.get('volume', 0)
        elif resp.status_code == 404:
            if not hasattr(check_recent_trades, '_error_shown'):
                print(f"❌ ERROR: Market {MARKET_TICKER} not found! Check the ticker.")
                print(f"   Response: {resp.text[:200]}")
                check_recent_trades._error_shown = True
            return
        else:
            # Other errors - don't spam, but log occasionally
            if not hasattr(check_recent_trades, '_error_count'):
                check_recent_trades._error_count = 0
            check_recent_trades._error_count += 1
            if check_recent_trades._error_count % 10 == 0:  # Log every 10th error
                print(f"⚠️  API Error {resp.status_code} (logged {check_recent_trades._error_count} times)")
            return
            
            # Initialize on first call
            if last_price is None:
                last_price = current_price
                last_volume = current_volume
                print(f"📊 Initialized tracking - Last Price: {current_price}, Volume: {current_volume}")
                print(f"   Watching for volume/price changes...")
                return
            
            # Also track volume_24h for more sensitive detection
            current_volume_24h = market_data.get('volume_24h', 0)
            if not hasattr(check_recent_trades, '_last_volume_24h'):
                check_recent_trades._last_volume_24h = current_volume_24h
            
            # Check if volume_24h changed (more reliable than total volume)
            volume_24h_changed = current_volume_24h != check_recent_trades._last_volume_24h
            
            # If price or volume changed, a trade occurred
            if current_price != last_price or current_volume != last_volume or volume_24h_changed:
                # Get current market state for the notification
                trade_info = {
                    "market_ticker": MARKET_TICKER,
                    "price": current_price,
                    "price_dollars": market_data.get('last_price_dollars', '0.0000'),
                    "previous_price": last_price,
                    "volume": current_volume,
                    "volume_24h": market_data.get('volume_24h', 0),
                    "yes_bid": market_data.get('yes_bid', 0),
                    "yes_ask": market_data.get('yes_ask', 0),
                    "no_bid": market_data.get('no_bid', 0),
                    "no_ask": market_data.get('no_ask', 0),
                    "timestamp": datetime.now().isoformat()
                }
                notify_order_fulfilled(trade_info)
                last_trade_time = time.time()
            
            last_price = current_price
            last_volume = current_volume
            check_recent_trades._last_volume_24h = current_volume_24h
    except Exception as e:
        # Silently handle errors - don't spam on network issues
        pass

async def monitor_market():
    """Main function to monitor the market for order fulfillments"""
    print(f"🔍 Monitoring market: {MARKET_TICKER}")
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)
    
    # Generate auth headers for WebSocket
    headers = get_auth_headers(method="GET", path="/trade-api/ws/v2")
    
    last_rest_poll = time.time()
    REST_POLL_INTERVAL = 2  # Poll REST API every 2 seconds for trades
    
    try:
        async with websockets.connect(WS_URL, extra_headers=headers) as websocket:
            print("✅ Connected to WebSocket!")
            
            # Subscribe to orderbook_delta channel (Kalshi doesn't have a "trades" channel)
            sub_msg = {
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": [MARKET_TICKER]
                }
            }
            await websocket.send(json.dumps(sub_msg))
            print(f"📤 Subscribed to orderbook_delta channel")
            await asyncio.sleep(0.5)
            
            print("📡 Listening for order fulfillments...")
            print("   (Press Ctrl+C to stop)")
            print("-" * 60)
            
            # Wait for subscription confirmations
            await asyncio.sleep(1.0)
            
            while True:
                # Poll REST API periodically for trades
                current_time = time.time()
                if current_time - last_rest_poll >= REST_POLL_INTERVAL:
                    check_recent_trades()
                    last_rest_poll = current_time
                
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
                    
                    # Check if data is a dict before calling .get()
                    if not isinstance(data, dict):
                        continue  # Skip non-dict messages (like numeric responses)
                    
                    msg_type = data.get("type") or data.get("cmd") or "unknown"
                    
                    # Handle orderbook delta - silently process (trade detection is via REST API polling)
                    if msg_type == "orderbook_delta":
                        # Orderbook updates are received but not printed
                        # Actual trade detection happens via REST API polling in check_recent_trades()
                        pass
                    
                    elif msg_type == "error":
                        error_msg = data.get('msg', {})
                        if isinstance(error_msg, dict):
                            error_code = error_msg.get('code', 'unknown')
                            error_text = error_msg.get('msg', str(error_msg))
                            print(f"❌ Error from server (code {error_code}): {error_text}")
                        else:
                            print(f"❌ Error from server: {data}")
                    
                    elif msg_type in ["subscribed", "subscription_confirmed", "ok"]:
                        # Subscription confirmations - just acknowledge
                        pass
                    
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    # Don't spam errors - only log unexpected issues
                    pass
                    
    except websockets.exceptions.ConnectionClosed:
        print("❌ Connection lost. Attempting to reconnect...")
        await asyncio.sleep(5)
        await monitor_market()  # Reconnect
    except KeyboardInterrupt:
        print("\n\n👋 Stopped monitoring.")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(monitor_market())
    except KeyboardInterrupt:
        print("\n👋 Exiting...")

