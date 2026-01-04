import requests
import json
from datetime import datetime, timezone, timedelta
from auth import get_auth_headers

def find_11pm_est_markets():
    """Find Bitcoin markets expiring at 11 PM EST"""
    print("🔍 Searching for Bitcoin markets expiring at 11 PM EST...")
    print("="*70)
    
    url = "https://api.elections.kalshi.com/trade-api/v2/markets"
    headers = get_auth_headers("GET", "/trade-api/v2/markets")
    
    # Try multiple parameter combinations to find Bitcoin markets
    params_list = [
        {"limit": 1000, "series_ticker": "KXBT"},
        {"limit": 1000, "series_ticker": "KXBTC"},
        {"limit": 2000},  # Without filter to see all markets
    ]
    
    markets = []
    for params in params_list:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                new_markets = data.get('markets', [])
                if new_markets:
                    # Filter for Bitcoin markets
                    btc_markets = [
                        m for m in new_markets
                        if 'KXBTC' in m.get('ticker', '') or 'KXBT' in m.get('ticker', '') or 'KXBT' in m.get('event_ticker', '')
                    ]
                    if btc_markets:
                        markets = btc_markets
                        print(f"✅ Found {len(markets)} Bitcoin markets using params: {params}")
                        break
        except:
            continue
    
    if not markets:
        # Final attempt - get all markets and filter
        try:
            response = requests.get(url, headers=headers, params={"limit": 2000}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                all_markets = data.get('markets', [])
                markets = [
                    m for m in all_markets
                    if 'bitcoin' in str(m).lower() or 'btc' in str(m).lower() or 'KXBT' in str(m)
                ]
                if markets:
                    print(f"✅ Found {len(markets)} Bitcoin markets (after filtering all markets)")
        except:
            pass
    
    if not markets:
        print("❌ No Bitcoin markets found. The API might be down or there are no active Bitcoin markets.")
        return
    
    print(f"📊 Found {len(markets)} total Bitcoin markets\n")
    
    # 11 PM EST = 04:00 UTC (next day) during standard time
    # 11 PM EST = 03:00 UTC (next day) during daylight time
    # We'll look for markets expiring around 3-4 AM UTC (which is 11 PM EST the previous day)
    
    eleven_pm_markets = []
    
    for market in markets:
        close_time_str = market.get('close_time', '')
        expected_exp_str = market.get('expected_expiration_time', '')
        
        if not close_time_str and not expected_exp_str:
            continue
        
        # Try to parse the expiration time
        exp_time_str = expected_exp_str or close_time_str
        try:
            # Parse ISO format: "2026-01-04T04:00:00Z"
            exp_dt = datetime.fromisoformat(exp_time_str.replace('Z', '+00:00'))
            
            # Check UTC hour directly - 11 PM EST = 4 AM UTC (next day), 11 PM EDT = 3 AM UTC (next day)
            # Markets expire at top of hour, so we check for 3:00 or 4:00 UTC
            utc_hour = exp_dt.hour
            utc_minute = exp_dt.minute
            
            # Also check the ticker - if it ends in "23" (like 0423), it's 11 PM
            ticker = market.get('ticker', '')
            is_11pm_by_ticker = False
            if 'JAN' in ticker:
                # Extract the day and hour from ticker like "KXBTC-26JAN0423-..."
                parts = ticker.split('-')
                if len(parts) >= 2:
                    date_part = parts[1]  # "26JAN0423"
                    if len(date_part) >= 6:
                        hour_part = date_part[-2:]  # Last 2 digits
                        if hour_part == '23':
                            is_11pm_by_ticker = True
            
            # 11 PM EST/EDT markets expire at 3:00 or 4:00 AM UTC, OR have "23" in ticker
            is_11pm_market = ((utc_hour == 3 or utc_hour == 4) and utc_minute == 0) or is_11pm_by_ticker
            
            if is_11pm_market:
                # Convert to EST to show in local time
                est_time = exp_dt - timedelta(hours=5)  # EST is UTC-5
                edt_time = exp_dt - timedelta(hours=4)  # EDT is UTC-4
                
                # Determine if it's EST or EDT based on which conversion gives us 23:00
                if est_time.hour == 23:
                    local_time_str = est_time.strftime('%Y-%m-%d %H:%M EST')
                elif edt_time.hour == 23:
                    local_time_str = edt_time.strftime('%Y-%m-%d %H:%M EDT')
                else:
                    local_time_str = f"{exp_dt.strftime('%Y-%m-%d %H:%M')} UTC"
                
                market_info = {
                    'ticker': market.get('ticker'),
                    'title': market.get('title'),
                    'market_type': market.get('market_type'),
                    'floor_strike': market.get('floor_strike'),
                    'cap_strike': market.get('cap_strike'),
                    'expiration_utc': exp_time_str,
                    'expiration_local': local_time_str,
                    'status': market.get('status')
                }
                eleven_pm_markets.append(market_info)
        except Exception as e:
            continue
    
    if not eleven_pm_markets:
        print("❌ No markets found expiring at 11 PM EST")
        print("\n💡 Showing all unique expiration times found (top of hour only):")
        
        # Show unique expiration times for reference
        unique_times = {}
        for market in markets[:100]:  # Check first 100 to avoid too much output
            exp_time_str = market.get('expected_expiration_time') or market.get('close_time', '')
            if exp_time_str:
                try:
                    exp_dt = datetime.fromisoformat(exp_time_str.replace('Z', '+00:00'))
                    est_time = exp_dt - timedelta(hours=5)
                    hour_key = est_time.strftime('%Y-%m-%d %H:00 EST')
                    if hour_key not in unique_times:
                        unique_times[hour_key] = market.get('ticker')
                except:
                    pass
        
        for time_key in sorted(unique_times.keys())[:20]:
            print(f"   {time_key} - Example: {unique_times[time_key]}")
        return
    
    # Sort by expiration date
    eleven_pm_markets.sort(key=lambda x: x['expiration_utc'])
    
    print(f"✅ Found {len(eleven_pm_markets)} markets expiring at 11 PM EST/EDT:\n")
    
    # Group by date
    by_date = {}
    for market in eleven_pm_markets:
        date_key = market['expiration_local'].split()[0]
        if date_key not in by_date:
            by_date[date_key] = []
        by_date[date_key].append(market)
    
    for date in sorted(by_date.keys()):
        print(f"\n📅 {date} at 11 PM EST/EDT:")
        print("-" * 70)
        
        for market in by_date[date]:
            ticker = market['ticker']
            title = market['title']
            m_type = market['market_type']
            floor = market['floor_strike']
            cap = market['cap_strike']
            
            print(f"   Ticker: {ticker}")
            print(f"   Title: {title}")
            print(f"   Type: {m_type}")
            
            if m_type == 'binary' and floor and not cap:
                # Binary "over X" market - only floor_strike, no cap_strike
                print(f"   Strike: ${floor:,.2f} (YES = price > ${floor:,.2f}, NO = price ≤ ${floor:,.2f})")
            elif m_type == 'range' and floor and cap:
                print(f"   Range: ${floor:,.2f} - ${cap:,.2f} (YES = price in range)")
            elif m_type == 'binary' and floor and cap:
                # This might be a range market incorrectly labeled as binary
                print(f"   Binary with range: ${floor:,.2f} - ${cap:,.2f}")
            
            print(f"   Status: {market['status']}")
            print()
    
    # Summary of tickers - filter for binary "over X" markets only
    binary_over_markets = [m for m in eleven_pm_markets if m['market_type'] == 'binary' and m['floor_strike'] and not m['cap_strike']]
    
    print("\n" + "="*70)
    print(f"📋 Binary 'Over X' Markets Only ({len(binary_over_markets)} markets):")
    print("="*70)
    for market in binary_over_markets:
        print(f"   {market['ticker']} - Over ${market['floor_strike']:,.2f}")
    
    print("\n" + "="*70)
    print("📋 All Tickers (11 PM EST/EDT markets):")
    print("="*70)
    for market in eleven_pm_markets:
        market_type = market['market_type']
        floor = market.get('floor_strike')
        cap = market.get('cap_strike')
        if market_type == 'binary' and floor and not cap:
            print(f"   {market['ticker']} (OVER ${floor:,.2f})")
        else:
            print(f"   {market['ticker']}")

if __name__ == "__main__":
    find_11pm_est_markets()
