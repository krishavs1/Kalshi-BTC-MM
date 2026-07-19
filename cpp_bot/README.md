# Kalshi BTC Bot (C++ Port)

This folder contains the standalone C++ Kalshi BTC market monitor and live execution engine.

## Latency model

- **Local hot path** (`ws_recv → decide`, and `ws_recv → submit kickoff`) is optimized for **microseconds**.
- **Exchange order RTT** over Kalshi HTTPS is still **milliseconds** (TLS + RSA-PSS auth + REST). Kalshi does not expose a co-located µs matching API to retail clients.

What we did for the local path:
- Cached RSA key in memory (`AuthSigner`) — no PEM reload per request
- Atomic orderbook slots (`HotBook`) — no mutex on book reads
- Recompute only sets touched by a delta
- HTTP keep-alive + TCP_NODELAY + connection warm-up
- Batch 3-leg create (one RTT / one signature when batch works)
- Decision lock released before network I/O
- Live `[latency]` p50/p90/p99 stats for `ws_to_decide` and `ws_to_submit_kick`

## Implemented

- RSA-PSS request signing for Kalshi headers
- REST market discovery (range + over-leg matching)
- WebSocket orderbook + fill subscriptions with reconnect/backoff
- Synthetic arbitrage signal construction from 3-leg combinations
- Deterministic execution pipeline (signal -> risk gate -> state machine decision)
- Live Kalshi V2 order submit / amend / cancel (`/portfolio/events/orders`)
- Cancel/replace on range price drift and partial-fill tracking
- Fee-aware risk controls + paper mode toggle
- Profit CSV logging + open-order polling

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
Set it to `true` and rebuild for a dry run.

## Notes

- Dependencies `nlohmann_json`, `websocketpp`, and `asio` are fetched by CMake.
- Size/risk knobs: `ORDER_SIZE`, `MIN_NET_EDGE_CENTS`, `MAX_OPEN_POSITIONS` in `src/config.hpp`.
