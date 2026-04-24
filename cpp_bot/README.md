# Kalshi BTC Bot (C++ Port)

This folder contains the standalone C++ Kalshi BTC market monitor.

## Implemented

- RSA-PSS request signing for Kalshi headers
- REST market discovery (range + over-leg matching)
- WebSocket orderbook delta subscription
- Synthetic arbitrage signal construction from 3-leg combinations
- Deterministic execution pipeline (signal -> risk gate -> state machine decision)
- Fee-aware risk controls (maker/taker fee deduction + max-open-position guard)
- Paper execution logic with order lifecycle state transitions
- Profit computation and throttled CSV logging
- Periodic market refresh and REST fallback polling

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

## Run

```bash
export KALSHI_KEY_ID="your-key-id"
export KALSHI_PRIVATE_KEY_PATH="./kalshi-key.pem"
./build/kalshi_bot_cpp
```

## Notes

- Dependencies `nlohmann_json`, `websocketpp`, and `asio` are fetched by CMake.
- Execution is currently paper-mode only (`ENABLE_PAPER_EXECUTION` in `src/config.hpp`).
