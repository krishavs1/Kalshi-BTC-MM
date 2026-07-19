# Kalshi BTC Bot (C++ Port)

This folder contains the standalone C++ Kalshi BTC market monitor and live execution engine.

## Implemented

- RSA-PSS request signing for Kalshi headers
- REST market discovery (range + over-leg matching)
- WebSocket orderbook + fill subscriptions with reconnect/backoff
- Synthetic arbitrage signal construction from 3-leg combinations
- Deterministic execution pipeline (signal -> risk gate -> state machine decision)
- Live Kalshi V2 order submit / amend / cancel (`/portfolio/events/orders`)
- Cancel/replace on range price drift and partial-fill tracking
- Fee-aware risk controls (maker/taker fee deduction + max-open-position guard)
- Paper mode toggle for dry runs
- Profit computation and throttled CSV logging
- Periodic market refresh, REST orderbook fallback, and open-order polling

## Prerequisites

- CMake 3.20+
- C++17 compiler
- OpenSSL
- libcurl

## Build

```bash
cd cpp_bot
cmake -S . -B build
cmake --build build -j
```

## Run (live)

```bash
export KALSHI_KEY_ID="your-key-id"
export KALSHI_PRIVATE_KEY_PATH="./kalshi-key.pem"
./build/kalshi_bot_cpp
```

Live submission is enabled by default (`ENABLE_PAPER_EXECUTION = false` in `src/config.hpp`).
To dry-run without sending orders, set `ENABLE_PAPER_EXECUTION` to `true` and rebuild.

## Notes

- Dependencies `nlohmann_json`, `websocketpp`, and `asio` are fetched by CMake.
- Orders use Kalshi V2 event-market endpoints with `bid`/`ask` book sides and dollar prices.
- Size/risk knobs: `ORDER_SIZE`, `MIN_NET_EDGE_CENTS`, `MAX_OPEN_POSITIONS` in `src/config.hpp`.
