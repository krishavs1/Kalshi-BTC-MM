#pragma once

namespace config {
static constexpr const char* WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2";
static constexpr const char* API_BASE = "https://api.elections.kalshi.com/trade-api/v2";
static constexpr const char* REST_URL_BASE = "https://api.elections.kalshi.com/trade-api/v2/markets";
static constexpr const char* ORDERS_PATH = "/trade-api/v2/portfolio/events/orders";
static constexpr const char* GET_ORDER_PATH_PREFIX = "/trade-api/v2/portfolio/orders/";

static constexpr int PROFIT_THRESHOLD_CENTS = 5;
static constexpr int MARKET_REFRESH_SECONDS = 300;
static constexpr int REST_POLL_SECONDS = 30;
static constexpr int ORDER_POLL_SECONDS = 5;
static constexpr int CSV_WRITE_MS = 1000;
static constexpr int TOP_RANGE_MARKETS = 5;

static constexpr double TAKER_FEE_BPS = 1.8;
static constexpr double MAKER_FEE_BPS = 0.9;
static constexpr double MIN_NET_EDGE_CENTS = 2.0;
static constexpr int MAX_OPEN_POSITIONS = 4;
static constexpr int ORDER_SIZE = 1;
static constexpr int REPLACE_MIN_TICK_CHANGE = 1;

// Live trading is on by default. Set ENABLE_PAPER_EXECUTION=true to dry-run.
static constexpr bool ENABLE_PAPER_EXECUTION = false;

static constexpr int WS_RECONNECT_BASE_MS = 1000;
static constexpr int WS_RECONNECT_MAX_MS = 30000;
static constexpr int HTTP_TIMEOUT_MS = 3000;
}  // namespace config
