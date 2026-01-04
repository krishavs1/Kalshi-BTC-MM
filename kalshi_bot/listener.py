import asyncio
import websockets
import json
import time
import requests
from auth import get_auth_headers
from selector import get_target_tickers

# We use the standard V2 Feed URL
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

async def start_listening():
    # 1. Get the Tickers we care about
    print("--- 1. FINDING MARKETS ---")
    try:
        clusters = get_target_tickers()
    except Exception as e:
        print(f"Error finding markets: {e}")
        return
    
    if not clusters:
        print("No markets found for the next hour. (Are we between hours?)")
        return

    # Let's watch the FIRST cluster found
    target_cluster = clusters[0] 
    my_tickers = [target_cluster['range'], target_cluster['leg_a'], target_cluster['leg_b']]
    
    print(f"--- 2. CONNECTING TO: {target_cluster['desc']} ---")
    print(f"Watching tickers: {my_tickers}")

    # 2. Generate Auth Headers for the Handshake
    # The websocket connection is technically a GET request to /v2/feed
    headers = get_auth_headers(method="GET", path="/trade-api/ws/v2")

    # 3. Connect with Headers
    print("--- 3. AUTHENTICATING ---")
    async with websockets.connect(WS_URL, extra_headers=headers) as websocket:
        print("✅ Connected! Sending subscription command...")

        # 4. Subscribe to orderbook_delta for all tickers (like test_stream.py)
        print("📤 Subscribing to orderbook_delta...")
        
        # Subscribe to orderbook_delta for all tickers at once
        sub_msg = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": my_tickers
            }
        }
        print(f"   Subscribing to: {my_tickers}")
        await websocket.send(json.dumps(sub_msg))
        print(f"   Sent: {json.dumps(sub_msg, indent=2)}")
        await asyncio.sleep(0.5)
        
        print("📡 All subscriptions sent! Waiting for data stream... (Press Ctrl+C to stop)")
        print("ℹ️  Note: orderbook_delta only sends messages when orders change.")
        print("   Place an order on Kalshi and watch for updates below.")
        print("------------------------------------------------")
        
        # Wait a moment for subscription confirmations
        await asyncio.sleep(1.0)
        
        # Also poll REST API periodically to check orderbook state
        # This helps verify if orders are actually appearing (WebSocket might not send all updates)
        last_poll_time = time.time()
        POLL_INTERVAL = 3  # Poll every 3 seconds

        while True:
            try:
                # Poll REST API periodically to check orderbook
                current_time = time.time()
                if current_time - last_poll_time >= POLL_INTERVAL:
                    try:
                        rest_headers = get_auth_headers('GET', f'/trade-api/v2/markets/{target_cluster["range"]}')
                        rest_url = f'https://api.elections.kalshi.com/trade-api/v2/markets/{target_cluster["range"]}'
                        rest_resp = requests.get(rest_url, headers=rest_headers, timeout=2)
                        if rest_resp.status_code == 200:
                            market_data = rest_resp.json().get('market', {})
                            yes_bid = market_data.get('yes_bid', 0)
                            yes_ask = market_data.get('yes_ask', 100)
                            no_bid = market_data.get('no_bid', 0)
                            no_ask = market_data.get('no_ask', 100)
                            # Only print if there's actual activity (not just default values)
                            if yes_bid > 0 or yes_ask < 100 or (no_bid > 0 and no_bid < 100) or (no_ask < 100 and no_ask > 0):
                                print(f"\n📊 REST API Poll - Orderbook activity detected:")
                                print(f"   Range {target_cluster['range']}: YES bid={yes_bid}, ask={yes_ask}, NO bid={no_bid}, ask={no_ask}")
                    except Exception as e:
                        pass  # Don't break on REST poll errors
                    last_poll_time = current_time
                
                # Set a short timeout for WebSocket receive so we can poll REST API
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue  # Timeout is fine, we'll poll REST and try again
                
                # Handle ping frames (Kalshi sends pings to keep connection alive)
                if isinstance(message, bytes):
                    # This might be a ping frame
                    continue
                
                data = json.loads(message)
                
                # Check for different message types
                msg_type = data.get("type") or data.get("cmd") or "unknown"
                
                # Debug: Print ALL messages except routine confirmations
                if msg_type not in ["subscribed", "subscription_confirmed", "ok"]:
                    # Always print orderbook_delta and any other data messages
                    if msg_type == "orderbook_delta":
                        print(f"\n🔥 ORDERBOOK DELTA RECEIVED!")
                    else:
                        print(f"\n📨 RAW MESSAGE (type: {msg_type}): {json.dumps(data, indent=2)}")
                
                if msg_type == "orderbook_delta":
                    msg_data = data.get('msg', {})
                    ticker = msg_data.get('market_ticker')
                    delta = msg_data.get('delta')
                    
                    if not ticker or not delta:
                        # Try alternative format
                        ticker = data.get('market_ticker')
                        delta = data.get('delta')
                    
                    # Debug: Print full delta message
                    print(f"🔥 ORDERBOOK DELTA RECEIVED!")
                    print(f"   Full message: {json.dumps(data, indent=2)}")
                    
                    # Identify who this update is for
                    role = "UNKNOWN"
                    if ticker == target_cluster['range']: 
                        role = "RANGE (Target)"
                    elif ticker == target_cluster['leg_a']: 
                        role = "LEG A (Lower)"
                    elif ticker == target_cluster['leg_b']: 
                        role = "LEG B (Upper)"
                    
                    print(f"[{role}] {ticker} - Price Update: {delta}")
                    
                elif msg_type == "orderbook_snapshot":
                    # Snapshots are just confirmations - don't print them (reduce noise)
                    # The actual orderbook data comes in deltas
                    pass
                    
                elif msg_type == "subscribed" or msg_type == "subscription_confirmed":
                    print(f"✅ Subscription confirmed")
                    
                elif msg_type == "ok":
                    # "ok" messages confirm successful subscriptions (silent now to reduce noise)
                    confirmed_tickers = data.get('msg', {}).get('market_tickers', [])
                    # Only print first confirmation
                    if not hasattr(start_listening, '_ok_printed'):
                        print(f"✅ All subscriptions confirmed")
                        start_listening._ok_printed = True
                    
                elif msg_type == "error":
                    print(f"❌ Error from server: {data.get('message', data)}")
                    
                else:
                    # Debug: Print ALL messages to see what we're getting
                    print(f"ℹ️  Message type '{msg_type}': {json.dumps(data, indent=2)}")

            except websockets.exceptions.ConnectionClosed:
                print("Connection lost. Reconnecting...")
                break
            except json.JSONDecodeError as e:
                print(f"⚠️  Non-JSON message received (might be ping/pong): {message[:100]}")
                continue
            except Exception as e:
                print(f"Error reading message: {e}")
                import traceback
                traceback.print_exc()
                break

if __name__ == "__main__":
    try:
        asyncio.run(start_listening())
    except KeyboardInterrupt:
        print("\nStopped.")