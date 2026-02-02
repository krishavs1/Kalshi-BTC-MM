"""Main monitoring loop for smart market monitoring"""
import asyncio
import websockets
import json
import time
import threading
import aiohttp
from config import WS_URL, PROFIT_THRESHOLD_CENTS
from auth import get_auth_headers
from marketFinder import find_and_setup_markets
from marketData import get_market_orderbook_async
from profitCalculator import calculate_profits, log_profits_to_csv

try:
    from webUi import update_data, run_server
    WEB_UI_AVAILABLE = True
except ImportError:
    WEB_UI_AVAILABLE = False
    print("⚠️  webUi.py not available - web UI disabled")

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
    
    last_market_refresh = time.time()
    MARKET_REFRESH_INTERVAL = 300  # Refresh market selection every 5 minutes (300 seconds)
    last_csv_write = time.time()
    CSV_WRITE_INTERVAL = 1.0  # Write to CSV at most once per second
    
    # In-memory orderbook cache: {ticker: {'yes_bid': int, 'no_bid': int, 'yes_ask': int, 'no_ask': int, 'last_price': int}}
    orderbook_cache = {}
    logged_samples = {}  # Track message counts: {"orderbook_snapshot_count": 5, "orderbook_delta_count": 3}
    
    # Create aiohttp session once for reuse (performance optimization)
    session = aiohttp.ClientSession()
    
    def update_orderbook_from_cache(ticker):
        """Get orderbook dict from cache, deriving asks from bids"""
        if ticker not in orderbook_cache:
            return None
        cached = orderbook_cache[ticker]
        # Derive asks from bids: yes_ask = 100 - no_bid, no_ask = 100 - yes_bid
        return {
            'yes_bid': cached['yes_bid'],
            'no_bid': cached['no_bid'],
            'yes_ask': 100 - cached['no_bid'],
            'no_ask': 100 - cached['yes_bid'],
            'last_price': cached.get('last_price', 0)
        }
    
    # Track whether we've logged "ready for trading" to avoid spam (reset when below threshold)
    ready_for_trading_logged = [False]  # Use list for nonlocal-like behavior in nested scope
    
    def recompute_and_update_profits(market_sets_list):
        """Recompute profits for all market sets and update UI/CSV"""
        nonlocal last_csv_write
        
        # Calculate profits for all sets
        all_profits = []
        all_sets_ui_data = []
        any_ready = False
        
        for i, market_set in enumerate(market_sets_list):
            range_ob = update_orderbook_from_cache(market_set['range_ticker'])
            lower_ob = update_orderbook_from_cache(market_set['lower_leg_ticker'])
            higher_ob = update_orderbook_from_cache(market_set['higher_leg_ticker'])
            
            profits = calculate_profits(range_ob, lower_ob, higher_ob)
            all_profits.append(profits)
            
            # Check if this set has profit > threshold (prepare for trading)
            ready = False
            if profits:
                p1, p2 = profits.get('profit1', 0), profits.get('profit2', 0)
                ready = p1 > PROFIT_THRESHOLD_CENTS or p2 > PROFIT_THRESHOLD_CENTS
                if ready:
                    any_ready = True
            
            # Prepare UI data
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
                },
                'ready_for_trading': ready
            })
        
        # Log when profit > threshold (once per crossing)
        if any_ready:
            if not ready_for_trading_logged[0]:
                ready_for_trading_logged[0] = True
                print(f"\n🚀 PROFIT > {PROFIT_THRESHOLD_CENTS}¢ — READY FOR TRADING\n")
        else:
            ready_for_trading_logged[0] = False
        
        # Update web UI
        if WEB_UI_AVAILABLE:
            try:
                update_data(all_sets_ui_data)
            except:
                pass  # Silently fail if UI not ready
        
        # Log to CSV (throttled to once per second)
        current_time = time.time()
        if csv_filename_ref[0] and (current_time - last_csv_write) >= CSV_WRITE_INTERVAL:
            last_csv_write = current_time
            log_profits_to_csv(csv_filename_ref[0], all_profits)
    
    async def initialize_orderbooks(tickers_list):
        """Initialize orderbook cache with initial REST calls"""
        print("📥 Initializing orderbook cache from REST API...")
        tasks = [get_market_orderbook_async(session, ticker) for ticker in tickers_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for ticker, result in zip(tickers_list, results):
            if isinstance(result, Exception) or result is None:
                # Initialize with defaults
                orderbook_cache[ticker] = {
                    'yes_bid': 0,
                    'no_bid': 0,
                    'last_price': 0
                }
            else:
                # Store bids in cache (asks will be derived)
                orderbook_cache[ticker] = {
                    'yes_bid': result.get('yes_bid', 0) or 0,
                    'no_bid': result.get('no_bid', 0) or 0,
                    'last_price': result.get('last_price', 0) or 0
                }
        print(f"✅ Initialized {len([r for r in results if not isinstance(r, Exception) and r is not None])} orderbooks")
    
    def process_orderbook_delta(data):
        """Process an orderbook_delta or orderbook_snapshot message and update cache
        Kalshi format: {'market_ticker': '...', 'yes': [[price, size], ...], 'no': [[price, size], ...]}
        Arrays are sorted by price (lowest to highest). First element = best bid, last element = best ask.
        """
        try:
            # Extract ticker
            ticker = data.get('market_ticker') or data.get('event_ticker') or data.get('ticker')
            if not ticker:
                return False
            
            # Extract best bids from yes/no arrays (first element = best bid)
            yes_bid = None
            no_bid = None
            
            # Parse YES array: first element is best YES bid
            if 'yes' in data and isinstance(data['yes'], list) and len(data['yes']) > 0:
                yes_entry = data['yes'][0]
                if isinstance(yes_entry, list) and len(yes_entry) > 0:
                    yes_bid = int(yes_entry[0])
            
            # Parse NO array: first element is best NO bid
            if 'no' in data and isinstance(data['no'], list) and len(data['no']) > 0:
                no_entry = data['no'][0]
                if isinstance(no_entry, list) and len(no_entry) > 0:
                    no_bid = int(no_entry[0])
            
            # Fallback: try direct yes_bid/no_bid fields (for REST API format)
            if yes_bid is None and 'yes_bid' in data:
                yes_bid = int(data['yes_bid'])
            if no_bid is None and 'no_bid' in data:
                no_bid = int(data['no_bid'])
            
            # Update cache (only update if we have new data)
            if ticker in orderbook_cache:
                if yes_bid is not None:
                    orderbook_cache[ticker]['yes_bid'] = yes_bid
                if no_bid is not None:
                    orderbook_cache[ticker]['no_bid'] = no_bid
                if 'last_price' in data:
                    orderbook_cache[ticker]['last_price'] = data.get('last_price', 0) or 0
                return True
            else:
                # Initialize if not in cache yet
                orderbook_cache[ticker] = {
                    'yes_bid': yes_bid if yes_bid is not None else 0,
                    'no_bid': no_bid if no_bid is not None else 0,
                    'last_price': data.get('last_price', 0) or 0
                }
                return True
        except Exception as e:
            # Silently handle parsing errors
            return False
    
    try:
        async with websockets.connect(WS_URL, extra_headers=headers) as websocket:
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
            
            # Initialize orderbook cache with initial REST calls
            await initialize_orderbooks(all_tickers_list)
            
            # Initial profit calculation and UI update
            recompute_and_update_profits(list(market_sets_ref))
            
            print("📡 Listening for orderbook deltas (real-time updates)...")
            print("   (Press Ctrl+C to stop)")
            print("-" * 60)
            
            while True:
                current_time = time.time()
                
                # Check if we need to refresh markets (every 5 minutes)
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
                        new_tickers_list = []
                        for market_set in market_sets_ref:
                            new_tickers_list.extend([
                                market_set['range_ticker'],
                                market_set['lower_leg_ticker'],
                                market_set['higher_leg_ticker']
                            ])
                        new_tickers_list = list(set(new_tickers_list))
                        
                        # Initialize orderbooks for new markets
                        await initialize_orderbooks(new_tickers_list)
                        
                        sub_msg = {
                            "id": 2,
                            "cmd": "subscribe",
                            "params": {
                                "channels": ["orderbook_delta"],
                                "market_tickers": new_tickers_list
                            }
                        }
                        await websocket.send(json.dumps(sub_msg))
                        print(f"📤 Re-subscribed to {len(new_tickers_list)} markets")
                        
                        # Recompute profits with new markets
                        recompute_and_update_profits(list(market_sets_ref))
                
                # Check for WebSocket messages (blocking - wait for messages)
                try:
                    message = await websocket.recv()
                except Exception:
                    continue
                
                # Handle ping frames
                if isinstance(message, bytes):
                    continue
                
                try:
                    data = json.loads(message)
                    if not isinstance(data, dict):
                        continue
                    
                    # Kalshi WebSocket messages use 'type' field for message type
                    msg_type = data.get("type")
                    
                    # Log sample messages to understand structure (log first 10 of each type)
                    if msg_type in ["orderbook_delta", "orderbook_snapshot"]:
                        count_key = f"{msg_type}_count"
                        if count_key not in logged_samples:
                            logged_samples[count_key] = 0
                        logged_samples[count_key] += 1
                        
                        if logged_samples[count_key] <= 10:  # Log first 10 messages of each type
                            print(f"\n{'='*60}")
                            print(f"📥 {msg_type} message #{logged_samples[count_key]} (full structure):")
                            print(f"{'='*60}")
                            print(json.dumps(data, indent=2))
                            print(f"{'='*60}\n")
                    
                    # Kalshi messages have nested structure: {type: "...", msg: {...actual data...}}
                    msg_data = data.get("msg", data)  # Fallback to data itself if no msg wrapper
                    
                    # Process orderbook snapshots and deltas
                    if msg_type in ["orderbook_delta", "orderbook_snapshot"]:
                        # Update cache from snapshot/delta (they have the same structure)
                        updated = process_orderbook_delta(msg_data)
                        
                        if updated:
                            # Recompute profits for all sets (quick operation)
                            recompute_and_update_profits(list(market_sets_ref))
                    
                    elif msg_type == "error":
                        error_msg = data.get('msg', {})
                        if isinstance(error_msg, dict):
                            error_code = error_msg.get('code', 'unknown')
                            if error_code != 8:  # Skip "Unknown channel" errors
                                print(f"❌ Error: {error_msg.get('msg', error_msg)}")
                    elif msg_type and msg_type not in ["subscription_confirmation", "heartbeat"]:
                        # Log unexpected message types (but only once per type to avoid spam)
                        pass  # Silent for now, can enable logging if needed
                    
                except Exception as e:
                    # Silently continue on parse errors
                    continue
    except websockets.exceptions.ConnectionClosed:
        print("❌ Connection lost. Attempting to reconnect...")
        await session.close()
        await asyncio.sleep(5)
        await monitor_markets(market_sets_ref, csv_filename_ref, date_str_ref)
    except KeyboardInterrupt:
        print("\n\n👋 Stopped monitoring.")
        await session.close()
    except Exception as e:
        print(f"❌ Error: {e}")
        await session.close()
        import traceback
        traceback.print_exc()
    finally:
        # Close the aiohttp session when done
        if not session.closed:
            await session.close()

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
    
    # Find initial markets (retry until markets are found)
    while True:
        market_sets, csv_filename, date_str = await find_and_setup_markets()
        if market_sets:
            break  # Found markets, exit retry loop
        
        # No markets found, wait and retry at next hour
        print(f"⏳ No markets found. Waiting to retry at next hour...")
        await asyncio.sleep(60)  # Wait 1 minute before checking again
    
    # Use lists/dicts as references so monitor_markets can update them
    market_sets_ref = market_sets  # Already a list, will be updated in place
    csv_filename_ref = [csv_filename]  # Wrap in list for reference update
    date_str_ref = [date_str]  # Wrap in list for reference update
    
    # Start monitoring (will refresh markets every 5 minutes)
    await monitor_markets(market_sets_ref, csv_filename_ref, date_str_ref)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Exiting...")
