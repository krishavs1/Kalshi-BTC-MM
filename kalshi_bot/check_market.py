import requests
import json
from auth import get_auth_headers

MARKET_TICKER = "KXBTCD-26JAN0414-T91249.99"

def get_market_info():
    """Fetch and display information about the specified market"""
    url = f"https://api.elections.kalshi.com/trade-api/v2/markets/{MARKET_TICKER}"
    headers = get_auth_headers("GET", f"/trade-api/v2/markets/{MARKET_TICKER}")
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            market = data.get('market', {})
            
            print("="*70)
            print(f"MARKET INFORMATION: {MARKET_TICKER}")
            print("="*70)
            print(f"\n📊 Title: {market.get('title', 'N/A')}")
            print(f"📝 Subtitle: {market.get('subtitle', 'N/A')}")
            print(f"🔢 Event Ticker: {market.get('event_ticker', 'N/A')}")
            print(f"📌 Market Type: {market.get('market_type', 'N/A')}")
            
            # Strike prices
            floor_strike = market.get('floor_strike')
            cap_strike = market.get('cap_strike')
            
            if floor_strike is not None:
                print(f"💰 Floor Strike: ${floor_strike:,.2f}")
            if cap_strike is not None:
                print(f"💰 Cap Strike: ${cap_strike:,.2f}")
            
            # Current prices
            yes_bid = market.get('yes_bid', 0)
            yes_ask = market.get('yes_ask', 100)
            no_bid = market.get('no_bid', 0)
            no_ask = market.get('no_ask', 100)
            
            print(f"\n💵 Current Prices:")
            print(f"   YES Bid: {yes_bid} | YES Ask: {yes_ask}")
            print(f"   NO Bid: {no_bid} | NO Ask: {no_ask}")
            
            # Market status
            print(f"\n📈 Market Status: {market.get('status', 'N/A')}")
            print(f"📅 Close Time: {market.get('close_time', 'N/A')}")
            print(f"📅 Expected Expiration: {market.get('expected_expiration_time', 'N/A')}")
            
            # Volume/Liquidity
            print(f"\n💹 Trading Activity:")
            print(f"   Volume (24h): {market.get('volume_24h', 0)}")
            notional = market.get('notional_value_dollars', 0)
            if isinstance(notional, str):
                try:
                    notional = float(notional)
                except:
                    notional = 0
            print(f"   Notional Value: ${notional:,.2f}")
            print(f"   Open Interest: {market.get('open_interest', 0)}")
            
            # Interpretation
            print(f"\n🔍 Interpretation:")
            if market.get('market_type') == 'binary':
                if floor_strike:
                    print(f"   This is a BINARY market.")
                    print(f"   YES = Bitcoin price will be ABOVE ${floor_strike:,.2f}")
                    print(f"   NO = Bitcoin price will be AT or BELOW ${floor_strike:,.2f}")
            elif market.get('market_type') == 'range':
                if floor_strike and cap_strike:
                    print(f"   This is a RANGE market.")
                    print(f"   YES = Bitcoin price will be BETWEEN ${floor_strike:,.2f} and ${cap_strike:,.2f}")
                    print(f"   NO = Bitcoin price will be OUTSIDE that range")
            
            # Parse ticker components
            print(f"\n🔤 Ticker Breakdown:")
            parts = MARKET_TICKER.split('-')
            if len(parts) >= 3:
                print(f"   Series: {parts[0]} (Bitcoin markets)")
                print(f"   Expiration: {parts[1]} (likely January 26, 4:00 AM)")
                print(f"   Strike/Identifier: {parts[2]}")
                if parts[2].startswith('B'):
                    strike_price = parts[2][1:]  # Remove 'B' prefix
                    try:
                        strike_num = float(strike_price)
                        print(f"   → Strike Price: ${strike_num:,.2f}")
                    except:
                        pass
            
            print("\n" + "="*70)
            
            # Full JSON dump for debugging
            print("\n📋 Full Market Data (JSON):")
            print(json.dumps(market, indent=2))
            
        else:
            print(f"❌ Error {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"❌ Error fetching market info: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    get_market_info()

