"""Main monitoring loop for smart market monitoring"""
import asyncio
import websockets
import json
import time
import threading
import aiohttp
from config import WS_URL
from auth import get_auth_headers
from market_finder import find_and_setup_markets
from market_data import get_market_orderbook_async
from trade_detector import check_market_trades
from profit_calculator import calculate_profits, log_profits_to_csv

try:
    from web_ui import update_data, run_server
    WEB_UI_AVAILABLE = True
except ImportError:
    WEB_UI_AVAILABLE = False
    print("⚠️  web_ui.py not available - web UI disabled")

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
    
    # Start monitoring (will refresh markets every 5 minutes)
    await monitor_markets(market_sets_ref, csv_filename_ref, date_str_ref)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Exiting...")
