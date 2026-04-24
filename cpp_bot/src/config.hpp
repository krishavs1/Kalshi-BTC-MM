#pragma once

#include <string>

namespace config {
static constexpr const char* WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2";
static constexpr const char* REST_URL_BASE = "https://api.elections.kalshi.com/trade-api/v2/markets";
static constexpr int PROFIT_THRESHOLD_CENTS = 5;
static constexpr int MARKET_REFRESH_SECONDS = 300;
static constexpr int REST_POLL_SECONDS = 30;
static constexpr int CSV_WRITE_MS = 1000;
static constexpr int TOP_RANGE_MARKETS = 5;
static constexpr double TAKER_FEE_BPS = 1.8;
static constexpr double MAKER_FEE_BPS = 0.9;
static constexpr double MIN_NET_EDGE_CENTS = 2.0;
static constexpr int MAX_OPEN_POSITIONS = 4;
static constexpr bool ENABLE_PAPER_EXECUTION = true;
}  // namespace config
