import asyncio
import websockets
import json
import requests
from auth import get_auth_headers

# --- CONSTANTS ---
# WE MUST USE THIS URL. It works for ALL markets (crypto, fed, etc.), not just elections.
REST_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

def get_loudest_market():
    print("🔎 Searching for the LOUDEST market on Kalshi...")
    
    # 1. Fetch ALL active markets
    params = {"limit": 100, "status": "active"}
    
    # AUTH FIX: We must sign the request even for public market data sometimes
    # depending on your account tier, but definitely for high-limit queries.
    path = "/trade-api/v2/markets"
    headers = get_auth_headers("GET", path)

    try:
        resp = requests.get(REST_URL, headers=headers, params=params)
        
        if resp.status_code != 200:
            print(f"❌ API Error {resp.status_code}: {resp.text}")
            return None
            
        markets = resp.json().get('markets', [])
    except Exception as e:
        print(f"❌ Error fetching markets: {e}")
        return None

    # 2. Sort by "Notional Value" (Total money at stake)
    # Filter out markets with 0 volume to avoid boring streams
    active_markets = [m for m in markets if m.get('notional_value_dollars', 0) > 0]
    
    if not active_markets:
        print("❌ No active markets found. (Is it the weekend/night? Markets might be quiet)")
        return None

    active_markets.sort(key=lambda x: float(x.get('notional_value_dollars', 0)), reverse=True)

    # Pick the #1 most active market
    top_market = active_markets[0]
    print(f"🏆 Loudest Market Found: {top_market['ticker']}")
    print(f"   Title: {top_market['title']}")
    print(f"   Volume: ${top_market.get('notional_value_dollars')}")
    
    return top_market['ticker']

async def listen_to_noise():
    ticker = get_loudest_market()
    if not ticker:
        return

    print(f"\n🎧 Connecting to {ticker}...")
    
    # Generate headers for WebSocket Handshake
    headers = get_auth_headers(method="GET", path="/trade-api/ws/v2")
    
    async with websockets.connect(WS_URL, extra_headers=headers) as websocket:
        print("✅ Connected! Subscribing...")
        
        # Subscribe to orderbook changes
        msg = {
            "id": 1, 
            "cmd": "subscribe", 
            "params": {"channels": ["orderbook_delta"], "market_tickers": [ticker]}
        }
        await websocket.send(json.dumps(msg))
        
        print("Waiting for updates... (Press Ctrl+C to stop)")
        print("-" * 50)
        
        while True:
            try:
                resp = await websocket.recv()
                data = json.loads(resp)
                
                if data.get('type') == 'orderbook_delta':
                    delta = data['msg']['delta']
                    # Just print the price of the YES contract to keep it readable
                    print(f"🔥 CHANGE: {delta}")
                elif data.get('type') == 'orderbook_snapshot':
                    print("📸 SNAPSHOT RECEIVED")
                elif data.get('type') == 'error':
                    print(f"❌ Stream Error: {data}")
                    
            except Exception as e:
                print(f"Error: {e}")
                break

if __name__ == "__main__":
    try:
        asyncio.run(listen_to_noise())
    except KeyboardInterrupt:
        print("\nstopped.")