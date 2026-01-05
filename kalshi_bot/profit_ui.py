import tkinter as tk
from tkinter import ttk
from datetime import datetime
import threading
import queue

class ProfitUI:
    """Real-time UI for displaying orderbook and profit data"""
    
    def __init__(self, data_queue=None):
        self.data_queue = data_queue if data_queue else queue.Queue()
        self.root = tk.Tk()
        self.root.title("Kalshi Profit Monitor")
        self.root.geometry("800x600")
        self.root.configure(bg='#1e1e1e')
        
        # Create main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Title
        title_label = tk.Label(main_frame, text="📊 Kalshi Profit Monitor", 
                              font=("Arial", 18, "bold"), bg='#1e1e1e', fg='white')
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))
        
        # Time display
        self.time_label = tk.Label(main_frame, text="", 
                                   font=("Arial", 12), bg='#1e1e1e', fg='#888888')
        self.time_label.grid(row=1, column=0, columnspan=2, pady=(0, 20))
        
        # Orderbook section
        orderbook_frame = ttk.LabelFrame(main_frame, text="Orderbook", padding="10")
        orderbook_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 20))
        
        # Market labels and data
        self.market_labels = {}
        markets = ['Range', 'Lower Leg', 'Higher Leg']
        self.market_tickers = {}
        
        for i, market_name in enumerate(markets):
            market_frame = ttk.Frame(orderbook_frame)
            market_frame.grid(row=i, column=0, sticky=(tk.W, tk.E), pady=5)
            
            # Market name
            name_label = tk.Label(market_frame, text=f"{market_name}:", 
                                 font=("Arial", 11, "bold"), bg='#1e1e1e', fg='white')
            name_label.grid(row=0, column=0, sticky=tk.W)
            
            # Ticker
            ticker_label = tk.Label(market_frame, text="", 
                                   font=("Arial", 9), bg='#1e1e1e', fg='#888888')
            ticker_label.grid(row=0, column=1, padx=(10, 0), sticky=tk.W)
            self.market_tickers[market_name] = ticker_label
            
            # YES/NO data
            data_label = tk.Label(market_frame, text="", 
                                 font=("Courier", 10), bg='#1e1e1e', fg='#00ff00',
                                 justify=tk.LEFT)
            data_label.grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=(20, 0))
            self.market_labels[market_name] = data_label
        
        # Profit section
        profit_frame = ttk.LabelFrame(main_frame, text="💰 Profit Opportunities", padding="10")
        profit_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 20))
        
        self.profit_labels = {}
        profit_names = [
            ("Profit 1", "Range YES overpriced", "#4A90E2"),
            ("Profit 2", "Range YES underpriced", "#50C878"),
            ("Profit 3", "Range NO overpriced", "#FF6B6B"),
            ("Profit 4", "Range NO underpriced", "#FFA500")
        ]
        
        for i, (profit_num, profit_desc, color) in enumerate(profit_names):
            profit_row = ttk.Frame(profit_frame)
            profit_row.grid(row=i, column=0, sticky=(tk.W, tk.E), pady=5)
            
            desc_label = tk.Label(profit_row, text=f"{profit_num} ({profit_desc}):", 
                                 font=("Arial", 10), bg='#1e1e1e', fg='white')
            desc_label.grid(row=0, column=0, sticky=tk.W)
            
            value_label = tk.Label(profit_row, text="0.00", 
                                  font=("Arial", 12, "bold"), bg='#1e1e1e', fg=color)
            value_label.grid(row=0, column=1, padx=(10, 0), sticky=tk.W)
            self.profit_labels[profit_num] = value_label
        
        # Configure grid weights
        main_frame.columnconfigure(0, weight=1)
        orderbook_frame.columnconfigure(0, weight=1)
        profit_frame.columnconfigure(0, weight=1)
        
        # Start update loop
        self.update_ui()
        
    def update_data(self, orderbooks, profits, tickers=None):
        """Update UI data (thread-safe)"""
        data = {
            'orderbooks': orderbooks,
            'profits': profits,
            'tickers': tickers or {}
        }
        self.data_queue.put(data)
    
    def update_ui(self):
        """Update UI from queue"""
        try:
            while True:
                data = self.data_queue.get_nowait()
                
                # Update time
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.time_label.config(text=f"Last Update: {current_time}")
                
                # Update orderbooks
                orderbooks = data.get('orderbooks', {})
                tickers = data.get('tickers', {})
                
                market_names = ['Range', 'Lower Leg', 'Higher Leg']
                market_keys = ['range', 'lower', 'higher']
                
                for market_name, market_key in zip(market_names, market_keys):
                    ob = orderbooks.get(market_key)
                    ticker = tickers.get(market_key, '')
                    
                    if ticker:
                        self.market_tickers[market_name].config(text=ticker)
                    
                    if ob:
                        yes_bid = ob.get('yes_bid', 0)
                        yes_ask = ob.get('yes_ask', 0)
                        no_bid = ob.get('no_bid', 0)
                        no_ask = ob.get('no_ask', 0)
                        last_price = ob.get('last_price', 0)
                        
                        text = f"  YES Bid: {yes_bid:3d} | YES Ask: {yes_ask:3d} | Last: {last_price:3d}\n"
                        text += f"  NO Bid:  {no_bid:3d} | NO Ask:  {no_ask:3d}"
                        self.market_labels[market_name].config(text=text)
                
                # Update profits
                profits = data.get('profits', {})
                profit_keys = ['profit1', 'profit2', 'profit3', 'profit4']
                profit_nums = ['Profit 1', 'Profit 2', 'Profit 3', 'Profit 4']
                
                for profit_key, profit_num in zip(profit_keys, profit_nums):
                    value = profits.get(profit_key, 0)
                    color = "#00ff00" if value > 0 else "#ff0000" if value < 0 else "#888888"
                    self.profit_labels[profit_num].config(
                        text=f"{value:+.2f}",
                        fg=color
                    )
                    
        except queue.Empty:
            pass
        
        # Schedule next update
        self.root.after(100, self.update_ui)
    
    def start(self):
        """Start the UI"""
        self.root.mainloop()
    
    def close(self):
        """Close the UI"""
        self.root.quit()
        self.root.destroy()


def main():
    """Test the UI"""
    import time
    import random
    
    ui = ProfitUI()
    
    # Simulate updates
    def update_loop():
        while True:
            orderbooks = {
                'range': {
                    'yes_bid': random.randint(20, 40),
                    'yes_ask': random.randint(30, 50),
                    'no_bid': random.randint(50, 70),
                    'no_ask': random.randint(60, 80),
                    'last_price': random.randint(25, 45)
                },
                'lower': {
                    'yes_bid': random.randint(70, 85),
                    'yes_ask': random.randint(80, 90),
                    'no_bid': random.randint(10, 20),
                    'no_ask': random.randint(15, 25),
                    'last_price': random.randint(75, 85)
                },
                'higher': {
                    'yes_bid': random.randint(40, 60),
                    'yes_ask': random.randint(50, 70),
                    'no_bid': random.randint(30, 50),
                    'no_ask': random.randint(40, 60),
                    'last_price': random.randint(45, 65)
                }
            }
            
            profits = {
                'profit1': random.uniform(-5, 5),
                'profit2': random.uniform(-5, 5),
                'profit3': random.uniform(-5, 5),
                'profit4': random.uniform(-5, 5)
            }
            
            tickers = {
                'range': 'KXBTC-26JAN0514-B93875',
                'lower': 'KXBTCD-26JAN0514-T93749.99',
                'higher': 'KXBTCD-26JAN0514-T93999.99'
            }
            
            ui.update_data(orderbooks, profits, tickers)
            time.sleep(1)
    
    # Start update thread
    update_thread = threading.Thread(target=update_loop, daemon=True)
    update_thread.start()
    
    ui.start()


if __name__ == "__main__":
    main()

