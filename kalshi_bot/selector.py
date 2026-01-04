import requests
from datetime import datetime, timedelta, timezone
from auth import get_auth_headers

def get_target_tickers():
    # 1. Calculate Current Hour Timestamp (markets expire at the top of the hour)
    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    # Check next 3 hours to catch markets expiring soon
    target_hours = [current_hour + timedelta(hours=i) for i in range(1, 4)]  # Next 1-3 hours
    
    print(f"🔎 Scanning for Bitcoin markets expiring in the next few hours...")

    # First, try to find Bitcoin events
    events_url = "https://api.elections.kalshi.com/trade-api/v2/events"
    events_params = {"limit": 500}
    events_response = requests.get(events_url, params=events_params)
    events_data = events_response.json()
    btc_events = [
        e for e in events_data.get('events', [])
        if 'bitcoin' in str(e).lower() or 'btc' in str(e).lower() or 'KXBT' in str(e)
    ]
    print(f"Found {len(btc_events)} Bitcoin events")
    
    # Get markets - try multiple approaches
    url = "https://api.elections.kalshi.com/trade-api/v2/markets"
    all_markets = []
    
    # Try multiple parameter combinations to find Bitcoin markets
    # First try with authentication (Bitcoin markets might require auth)
    headers = get_auth_headers(method="GET", path="/trade-api/v2/markets")
    
    # Calculate timestamp range for the target hours
    min_ts = int(min(target_hours).timestamp())
    max_ts = int((max(target_hours) + timedelta(hours=1)).timestamp())
    
    for params in [
        {"limit": 1000, "series_ticker": "KXBT", "min_close_ts": min_ts, "max_close_ts": max_ts},  # With time range
        {"limit": 1000, "series_ticker": "KXBT"},  # Try series_ticker first
        {"limit": 2000, "series_ticker": "KXBT"},  # Larger limit
        {"limit": 1000, "min_close_ts": min_ts, "max_close_ts": max_ts},  # Time range without series
        {"limit": 1000},  # No filter
    ]:
        try:
            response = requests.get(url, params=params, headers=headers)
            data = response.json()
            if 'error' not in data:
                markets = data.get('markets', [])
                if markets:  # Only use if we got results
                    all_markets = markets
                    print(f"  Using params: {params}")
                    break
        except Exception as e:
            print(f"  Error with params {params}: {e}")
            continue
    
    print(f"Retrieved {len(all_markets)} total markets")
    
    # Debug: Show sample market structure
    if all_markets:
        sample = all_markets[0]
        print(f"Sample market keys: {list(sample.keys())[:10]}")
        print(f"Sample ticker: {sample.get('ticker', '')[:50]}")
        print(f"Sample title: {sample.get('title', '')[:60]}")
    
    # Filter for Bitcoin markets - check multiple fields
    btc_markets = []
    for m in all_markets:
        ticker = m.get('ticker', '')
        event_ticker = m.get('event_ticker', '')
        title = m.get('title', '').lower()
        subtitle = m.get('subtitle', '').lower()
        
        # Check all string fields for Bitcoin-related terms
        market_str = str(m).lower()
        
        if ('KXBT' in ticker or 'KXBT' in event_ticker or
            'bitcoin' in title or 'btc' in title or
            'bitcoin' in subtitle or 'btc' in subtitle or
            'bitcoin' in market_str or 'btc' in market_str):
            btc_markets.append(m)
            if len(btc_markets) <= 3:  # Show first few matches
                print(f"  Found BTC market: {ticker} - {title[:50]}")
    
    print(f"Found {len(btc_markets)} Bitcoin markets total")
    
    if len(btc_markets) == 0:
        return []
    
    # Separate range markets from binary markets FIRST (before time filtering)
    range_markets = []
    binary_markets = {}  # Map floor_strike -> ticker
    
    for m in btc_markets:
        market_type = m.get('market_type', '')
        floor_strike = m.get('floor_strike')
        cap_strike = m.get('cap_strike')
        
        # Range markets have both floor_strike and cap_strike
        if cap_strike is not None and floor_strike is not None:
            range_markets.append(m)
        # Binary markets have only floor_strike (YES to price > floor_strike)
        # Store all binaries - we'll need them to match with ranges
        elif market_type == 'binary' and floor_strike is not None:
            # Handle float precision - use the floor_strike as key
            binary_markets[floor_strike] = m.get('ticker')
    
    print(f"Found {len(range_markets)} range markets and {len(binary_markets)} binary markets (before time filter)")
    
    # Now filter range markets by expiration time
    # Use expected_expiration_time (sooner expiration) or expiration_time
    print(f"Filtering {len(range_markets)} range markets by expiration time...")
    filtered_range_markets = []
    now = datetime.now(timezone.utc)
    
    for m in range_markets:
        # Try expected_expiration_time first (the actual expiration), then expiration_time
        exp_time_str = m.get('expected_expiration_time', '') or m.get('expiration_time', '') or m.get('close_time', '')
        if exp_time_str:
            try:
                exp_dt = datetime.fromisoformat(exp_time_str.replace('Z', '+00:00'))
                # Include markets expiring in the next 6 hours
                time_until_exp = (exp_dt - now).total_seconds()
                if 0 < time_until_exp < 21600:  # Between now and 6 hours from now
                    filtered_range_markets.append(m)
            except Exception as e:
                # If parsing fails, include it anyway
                filtered_range_markets.append(m)
        else:
            # No expiration time, include it
            filtered_range_markets.append(m)
    
    print(f"Found {len(filtered_range_markets)} range markets matching time window")
    
    if len(filtered_range_markets) == 0:
        # Show what expiration times we actually have
        print("Available expiration times in range markets:")
        for m in range_markets[:5]:
            print(f"  {m.get('ticker')}: {m.get('close_time')} or {m.get('expiration_time')}")
        return []
    
    # Use filtered range markets
    range_markets = filtered_range_markets
    
    print(f"Found {len(range_markets)} range markets and {len(binary_markets)} binary markets")
    
    if len(range_markets) == 0:
        print("❌ No range markets found")
        return []
    
    # Get current Bitcoin price
    # Strategy: Use known current price if available, otherwise estimate from ranges
    # TODO: You could fetch this from a price API or pass it as a parameter
    KNOWN_CURRENT_PRICE = 91146  # Current Bitcoin price - UPDATE THIS REGULARLY
    
    current_price = None
    
    # First, try to find ranges with trading activity that might contain the known price
    active_ranges = [r for r in range_markets 
                    if (r.get('yes_bid', 0) > 0 or r.get('no_bid', 0) > 0)
                    and r.get('floor_strike', 0) <= KNOWN_CURRENT_PRICE <= r.get('cap_strike', 0)]
    
    if active_ranges:
        # Use the known price directly
        current_price = KNOWN_CURRENT_PRICE
    else:
        # Check if any range contains the known price
        containing_ranges = [r for r in range_markets 
                           if r.get('floor_strike', 0) <= KNOWN_CURRENT_PRICE <= r.get('cap_strike', 0)]
        if containing_ranges:
            current_price = KNOWN_CURRENT_PRICE
        else:
            # Fallback: estimate from ranges in 90k-92k band
            ranges_90k = [r for r in range_markets 
                         if 90000 <= (r.get('floor_strike', 0) + r.get('cap_strike', 0)) / 2 <= 92000]
            if ranges_90k:
                mid_prices = [(r.get('floor_strike', 0) + r.get('cap_strike', 0)) / 2 for r in ranges_90k]
                current_price = sorted(mid_prices)[len(mid_prices) // 2]
            else:
                current_price = KNOWN_CURRENT_PRICE
    
    print(f"📊 Estimated current Bitcoin price: ${current_price:,.2f}")
    
    # Calculate volatility for each range market
    # Volatility factors:
    # 1. Bid-ask spread (tighter spread = more liquid = potentially more volatile)
    # 2. Proximity to current price (closer = more relevant)
    # 3. Liquidity/volume (higher = more active)
    range_volatilities = []
    print(f"Calculating volatility for {len(range_markets)} range markets...")
    for r in range_markets:
        floor = r.get('floor_strike', 0)
        cap = r.get('cap_strike', 0)
        mid_price = (floor + cap) / 2 if (floor + cap) > 0 else 1
        range_spread = cap - floor
        
        # Calculate bid-ask spread (volatility indicator)
        yes_bid = r.get('yes_bid', 0) or 0
        yes_ask = r.get('yes_ask', 0) or 100
        no_bid = r.get('no_bid', 0) or 0
        no_ask = r.get('no_ask', 0) or 100
        
        # Use the tighter spread (more liquid side)
        yes_spread = yes_ask - yes_bid if yes_ask > yes_bid else 100
        no_spread = no_ask - no_bid if no_ask > no_bid else 100
        bid_ask_spread = min(yes_spread, no_spread)
        
        # Distance from current price (closer = better)
        price_distance = abs(mid_price - current_price)
        price_proximity_score = 1 / (1 + price_distance / 1000)  # Decay with distance
        
        # Liquidity indicators (volume_24h only counts completed trades, not open orders)
        # Use multiple indicators:
        # - notional_value: total value in market (better indicator of activity)
        # - open_interest: open positions
        # - liquidity: market maker liquidity
        # - volume_24h: completed trades (may be 0 even if market is active)
        notional_value = float(r.get('notional_value_dollars', '0') or 0)
        open_interest = r.get('open_interest', 0) or 0
        liquidity = r.get('liquidity', 0) or 0
        volume_24h = r.get('volume_24h', 0) or 0
        
        # Combined liquidity score (notional value is most important)
        liquidity_score = 1 + (notional_value / 100) + (open_interest / 1000) + (liquidity / 10000) + (volume_24h / 10000)
        
        # Volatility score: 
        # - Higher if bid-ask spread is tight (more liquid)
        # - Higher if close to current price (especially if price is WITHIN the range)
        # - Higher if has liquidity
        # Penalize ranges far from current price, but prioritize ranges that CONTAIN current price
        price_in_range = floor <= current_price <= cap if current_price else False
        
        # Strictly filter out ranges far from current price
        # Only consider ranges within $2,500 of current price (or that contain it)
        if price_distance > 2500 and not price_in_range:
            volatility_score = 0  # Skip ranges too far from current price
        else:
            # Base score: inverse bid-ask spread (tighter = better) * proximity * liquidity
            # Note: bid_ask_spread can be 100 if no bids/asks, so (100 - 100) = 0
            # We need to handle the case where there's no trading activity
            if bid_ask_spread >= 100:
                # No trading activity, use a base score based on proximity only
                base_score = price_proximity_score * liquidity_score * 10  # Small base score
            else:
                base_score = (100 - bid_ask_spread) * price_proximity_score * liquidity_score
            
            # CRITICAL: Massive bonus if current price is within the range
            # This ensures we always pick the range containing the current price
            if price_in_range:
                volatility_score = base_score * 100  # Huge multiplier if price is in range
            else:
                # Penalize distance more aggressively
                distance_penalty = max(0, 1 - (price_distance / 2000))  # Decay faster
                volatility_score = base_score * distance_penalty
        
        range_volatilities.append({
            'market': r,
            'volatility': volatility_score,
            'spread': range_spread,
            'bid_ask_spread': bid_ask_spread,
            'mid_price': mid_price,
            'price_distance': price_distance,
            'notional_value': notional_value,
            'open_interest': open_interest,
            'liquidity': liquidity,
            'volume_24h': volume_24h
        })
    
    # Sort by volatility (highest first)
    # This prioritizes ranges containing current price, then by proximity and liquidity
    range_volatilities.sort(key=lambda x: x['volatility'], reverse=True)
    
    # Filter out ranges with 0 volatility (too far from price)
    range_volatilities = [rv for rv in range_volatilities if rv['volatility'] > 0]
    
    if len(range_volatilities) == 0:
        print("❌ No range markets found near current price")
        print("Debug: Checking why ranges were filtered...")
        # Show some sample ranges that were filtered
        sample_filtered = [rv for rv in range_volatilities[:5] if rv['volatility'] == 0] if len([rv for rv in range_volatilities if rv['volatility'] == 0]) > 0 else []
        if not sample_filtered:
            # All had 0 volatility, show why
            all_ranges_check = range_volatilities[:5] if range_volatilities else []
            for rv in all_ranges_check:
                m = rv['market']
                print(f"  {m.get('ticker')}: mid=${rv['mid_price']:,.2f}, dist=${rv['price_distance']:,.2f}, vol_score={rv['volatility']:.2f}")
        return []
    
    print(f"\nTop range markets by volatility (near current price):")
    for i, rv in enumerate(range_volatilities[:5]):
        m = rv['market']
        print(f"  {i+1}. {m.get('ticker')}: ${m.get('floor_strike')} to ${m.get('cap_strike')}")
        print(f"      Mid: ${rv['mid_price']:,.2f}, Distance: ${rv['price_distance']:,.2f}, Bid-Ask: {rv['bid_ask_spread']:.1f}")
        print(f"      Notional: \${rv['notional_value']:.2f}, Open Interest: {rv['open_interest']}, Vol Score: {rv['volatility']:.2f}")
    
    # Take the highest volatility range market
    best_range = range_volatilities[0]['market']
    floor_strike = best_range.get('floor_strike')
    cap_strike = best_range.get('cap_strike')
    
    print(f"\n✅ Selected range: {best_range.get('ticker')} - ${floor_strike} to ${cap_strike}")
    
    # Find the two binary leg markets
    # Leg A: YES to price > floor_strike (binary with floor_strike = lower bound)
    # Leg B: NO to price > cap_strike (binary with floor_strike = upper bound, but we take NO side)
    # Need to handle float precision - try exact match first, then closest match
    leg_a_ticker = binary_markets.get(floor_strike)
    leg_b_ticker = binary_markets.get(cap_strike)
    
    # If exact match not found, try to find closest strike
    if not leg_a_ticker:
        closest_strike = min(binary_markets.keys(), key=lambda x: abs(x - floor_strike) if x is not None else float('inf'))
        if abs(closest_strike - floor_strike) < 0.01:  # Within 1 cent
            leg_a_ticker = binary_markets[closest_strike]
            print(f"  Found approximate leg A: ${closest_strike} (target was ${floor_strike})")
    
    if not leg_b_ticker:
        closest_strike = min(binary_markets.keys(), key=lambda x: abs(x - cap_strike) if x is not None else float('inf'))
        if abs(closest_strike - cap_strike) < 0.01:  # Within 1 cent
            leg_b_ticker = binary_markets[closest_strike]
            print(f"  Found approximate leg B: ${closest_strike} (target was ${cap_strike})")
    
    if not leg_a_ticker:
        print(f"⚠️  Warning: Could not find binary leg A for floor_strike ${floor_strike}")
    if not leg_b_ticker:
        print(f"⚠️  Warning: Could not find binary leg B for cap_strike ${cap_strike}")
    
    if leg_a_ticker and leg_b_ticker:
        cluster = {
            "range": best_range.get('ticker'),
            "leg_a": leg_a_ticker,  # YES to price > floor_strike
            "leg_b": leg_b_ticker,  # Binary market for cap_strike (we'll take NO side)
            "desc": f"${floor_strike} to ${cap_strike}",
            "floor_strike": floor_strike,
            "cap_strike": cap_strike
        }
        print(f"✅ Found cluster:")
        print(f"  Range: {cluster['range']}")
        print(f"  Leg A (YES > ${floor_strike}): {cluster['leg_a']}")
        print(f"  Leg B (NO > ${cap_strike}): {cluster['leg_b']}")
        return [cluster]
    else:
        print("❌ Could not find both leg markets")
        return []

if __name__ == "__main__":
    found = get_target_tickers()
    if not found:
        print("❌ No matching markets found. (Is the API down or are we between hours?)")
    else:
        print(f"✅ Found {len(found)} tradeable clusters.")
        for c in found:
            print(f"Cluster: {c['desc']}")
            print(f"  Range: {c['range']}")
            print(f"  Leg A: {c['leg_a']}")
            print(f"  Leg B: {c['leg_b']}")

