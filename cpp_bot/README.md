# Kalshi BTC Bot (C++ Port)

This folder contains the standalone C++ Kalshi BTC market monitor.

## Implemented

- RSA-PSS request signing for Kalshi headers
- REST market discovery (range + over-leg matching)
- WebSocket orderbook delta subscription
- Profit computation and CSV logging
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
