"""Configuration constants and global state for the market monitor"""

# API Configuration
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
REST_URL_BASE = "https://api.elections.kalshi.com/trade-api/v2/markets"

# Track recent trades to avoid duplicate notifications
seen_trades = {}
last_trade_time = {}
last_price = {}
last_volume = {}
last_orderbook = {}  # Track previous orderbook state

