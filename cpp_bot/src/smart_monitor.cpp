#include "smart_monitor.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <iostream>
#include <set>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>
#include <websocketpp/client.hpp>
#include <websocketpp/config/asio_client.hpp>

#include "auth.hpp"
#include "config.hpp"
#include "execution_engine.hpp"
#include "hot_book.hpp"
#include "http_client.hpp"
#include "latency.hpp"
#include "market_finder.hpp"
#include "order_client.hpp"
#include "profit_calculator.hpp"

using WsClient = websocketpp::client<websocketpp::config::asio_tls_client>;

namespace {
Orderbook rest_orderbook(HttpClient& http, AuthSigner& signer, const std::string& ticker) {
  auto headers = signer.sign("GET", "/trade-api/v2/markets/" + ticker);
  auto json = http.get_json(std::string(config::REST_URL_BASE) + "/" + ticker, headers, 1500);
  if (!json || !json->contains("market")) {
    return {};
  }
  const auto& m = (*json)["market"];
  Orderbook ob;
  ob.yes_bid = m.value("yes_bid", 0);
  ob.no_bid = m.value("no_bid", 0);
  ob.yes_ask = m.value("yes_ask", 100);
  ob.no_ask = m.value("no_ask", 100);
  ob.last_price = m.value("last_price", 0);
  return ob;
}

int64_t wall_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

int64_t wall_us() {
  return std::chrono::duration_cast<std::chrono::microseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
}  // namespace

void run_monitor(const std::string& key_id, const std::string& private_key_path) {
  HttpClient http;
  AuthSigner signer(key_id, private_key_path);
  OrderClient order_client(http, signer);

  LatencyStats decide_stats("ws_to_decide");
  LatencyStats submit_kick_stats("ws_to_submit_kick");

  if (config::ENABLE_PAPER_EXECUTION) {
    std::cout << "Mode: PAPER (no live orders)\n";
  } else {
    std::cout << "Mode: LIVE order submission enabled\n";
    std::cout << "Warming TLS/TCP connections...\n";
    order_client.warm_connections();
  }
  std::cout << "Hot path target: ws→decide in microseconds; exchange HTTP RTT remains ms-bound.\n";

  std::optional<MarketDiscoveryResult> discovery;
  while (!discovery) {
    discovery = find_and_setup_markets(http, key_id, private_key_path, true);
    if (!discovery) {
      std::cout << "No markets found. Retrying in 60 seconds...\n";
      std::this_thread::sleep_for(std::chrono::seconds(60));
    }
  }

  HotBook books;
  books.reset(discovery->market_sets);
  std::string csv_filename = discovery->csv_filename;
  std::string date_str = discovery->date_str;

  ExecutionEngine execution(
      RiskConfig{
          config::MAKER_FEE_BPS,
          config::TAKER_FEE_BPS,
          config::MIN_NET_EDGE_CENTS,
          config::MAX_OPEN_POSITIONS,
          config::ENABLE_PAPER_EXECUTION,
          config::ORDER_SIZE,
          config::REPLACE_MIN_TICK_CHANGE,
      },
      config::ENABLE_PAPER_EXECUTION ? nullptr : &order_client, &decide_stats, &submit_kick_stats);

  int64_t last_csv_ms = wall_ms();
  std::atomic<int64_t> last_ws_ms{wall_ms()};
  std::atomic<int64_t> last_refresh_ms{wall_ms()};
  std::atomic<int64_t> last_order_poll_ms{wall_ms()};
  std::atomic<bool> ws_connected{false};

  auto refresh_cache = [&]() {
    for (const auto& s : books.sets()) {
      for (const auto& t : {s.range_ticker, s.lower_leg_ticker, s.higher_leg_ticker}) {
        const int idx = books.index_of(t);
        if (auto* b = books.book_at(idx)) {
          b->store(rest_orderbook(http, signer, t));
        }
      }
    }
  };

  auto recompute_sets = [&](const int* set_idxs, int n, int64_t ws_recv_ns) {
    std::vector<ProfitPair> all_for_csv;
    all_for_csv.resize(static_cast<size_t>(books.set_count()));

    // Always fill CSV snapshot from atomics (no mutex).
    for (int i = 0; i < books.set_count(); ++i) {
      int ri, li, hi;
      books.set_indices(i, &ri, &li, &hi);
      const Orderbook ro = books.book_at(ri)->snapshot();
      const Orderbook lo = books.book_at(li)->snapshot();
      const Orderbook ho = books.book_at(hi)->snapshot();
      all_for_csv[static_cast<size_t>(i)] = calculate_profits(&ro, &lo, &ho);
    }

    const int64_t decision_ts_us = wall_us();
    for (int k = 0; k < n; ++k) {
      const int i = set_idxs[k];
      int ri, li, hi;
      books.set_indices(i, &ri, &li, &hi);
      const Orderbook ro = books.book_at(ri)->snapshot();
      const Orderbook lo = books.book_at(li)->snapshot();
      const Orderbook ho = books.book_at(hi)->snapshot();
      const auto& p = all_for_csv[static_cast<size_t>(i)];
      if (p.valid &&
          (p.profit1 > config::PROFIT_THRESHOLD_CENTS || p.profit2 > config::PROFIT_THRESHOLD_CENTS)) {
        std::cout << "PROFIT > " << config::PROFIT_THRESHOLD_CENTS << "c - READY FOR TRADING\n";
      }
      execution.evaluate_set("set_" + std::to_string(i + 1), books.sets()[static_cast<size_t>(i)], p,
                            ro, lo, ho, decision_ts_us, ws_recv_ns);
    }

    const int64_t current_ms = wall_ms();
    if ((current_ms - last_csv_ms) >= config::CSV_WRITE_MS) {
      log_profits_to_csv(csv_filename, all_for_csv);
      last_csv_ms = current_ms;
    }
  };

  auto recompute_all = [&](int64_t ws_recv_ns) {
    int idxs[HotBook::kMaxTickers];
    const int n = books.set_count();
    for (int i = 0; i < n; ++i) idxs[i] = i;
    recompute_sets(idxs, n, ws_recv_ns);
  };

  refresh_cache();
  recompute_all(0);

  int reconnect_delay_ms = config::WS_RECONNECT_BASE_MS;

  while (true) {
    WsClient ws;
    ws.clear_access_channels(websocketpp::log::alevel::all);
    ws.clear_error_channels(websocketpp::log::elevel::all);
    ws.init_asio();
    ws.set_tls_init_handler([](websocketpp::connection_hdl) {
      namespace asio = websocketpp::lib::asio;
      auto ctx = websocketpp::lib::make_shared<asio::ssl::context>(asio::ssl::context::tlsv12_client);
      ctx->set_default_verify_paths();
      return ctx;
    });

    auto ws_headers = signer.sign("GET", "/trade-api/ws/v2");
    websocketpp::lib::error_code ec;
    auto conn = ws.get_connection(config::WS_URL, ec);
    if (ec) {
      std::cerr << "WebSocket setup failed: " << ec.message() << "\n";
      std::this_thread::sleep_for(std::chrono::milliseconds(reconnect_delay_ms));
      reconnect_delay_ms = std::min(reconnect_delay_ms * 2, config::WS_RECONNECT_MAX_MS);
      continue;
    }
    for (const auto& [k, v] : ws_headers) {
      conn->append_header(k, v);
    }

    ws.set_open_handler([&](websocketpp::connection_hdl hdl) {
      ws_connected = true;
      reconnect_delay_ms = config::WS_RECONNECT_BASE_MS;
      last_ws_ms.store(wall_ms());
      std::cout << "WebSocket connected\n";

      std::set<std::string> tickers;
      for (const auto& s : books.sets()) {
        tickers.insert(s.range_ticker);
        tickers.insert(s.lower_leg_ticker);
        tickers.insert(s.higher_leg_ticker);
      }
      nlohmann::json sub_ob{
          {"id", 1},
          {"cmd", "subscribe"},
          {"params", {{"channels", {"orderbook_delta"}}, {"market_tickers", tickers}}},
      };
      ws.send(hdl, sub_ob.dump(), websocketpp::frame::opcode::text);

      nlohmann::json sub_fill{
          {"id", 2},
          {"cmd", "subscribe"},
          {"params", {{"channels", {"fill"}}}},
      };
      ws.send(hdl, sub_fill.dump(), websocketpp::frame::opcode::text);
    });

    ws.set_fail_handler([&](websocketpp::connection_hdl) {
      ws_connected = false;
      std::cerr << "WebSocket connection failed\n";
    });

    ws.set_close_handler([&](websocketpp::connection_hdl) {
      ws_connected = false;
      std::cerr << "WebSocket closed\n";
    });

    ws.set_message_handler([&](websocketpp::connection_hdl, WsClient::message_ptr msg) {
      const int64_t t0 = mono_ns();
      last_ws_ms.store(wall_ms());
      try {
        auto data = nlohmann::json::parse(msg->get_payload());
        const std::string type = data.value("type", "");

        if (type == "fill") {
          auto m = data.contains("msg") ? data["msg"] : data;
          const std::string order_id = m.value("order_id", "");
          if (order_id.empty()) return;
          double fill_count = 0.0;
          if (m.contains("count_fp") && m["count_fp"].is_string()) {
            fill_count = std::stod(m["count_fp"].get<std::string>());
          } else if (m.contains("count")) {
            fill_count = m["count"].is_number() ? m["count"].get<double>()
                                                : std::stod(m["count"].get<std::string>());
          }
          bool has_remaining = false;
          double remaining = 0.0;
          if (m.contains("remaining_count_fp")) {
            has_remaining = true;
            remaining = m["remaining_count_fp"].is_number()
                            ? m["remaining_count_fp"].get<double>()
                            : std::stod(m["remaining_count_fp"].get<std::string>());
          } else if (m.contains("remaining_count")) {
            has_remaining = true;
            remaining = m["remaining_count"].is_number()
                            ? m["remaining_count"].get<double>()
                            : std::stod(m["remaining_count"].get<std::string>());
          }
          execution.on_fill_update(order_id, fill_count, has_remaining, remaining);
          return;
        }

        if (type != "orderbook_delta" && type != "orderbook_snapshot") {
          return;
        }
        auto m = data.contains("msg") ? data["msg"] : data;
        const std::string ticker =
            m.value("market_ticker", m.value("event_ticker", m.value("ticker", std::string{})));
        if (ticker.empty()) return;

        const int tidx = books.index_of(ticker);
        auto* book = books.book_at(tidx);
        if (!book) return;

        int yes_bid = -1;
        int no_bid = -1;
        if (m.contains("yes") && m["yes"].is_array() && !m["yes"].empty() && m["yes"][0].is_array() &&
            !m["yes"][0].empty()) {
          yes_bid = m["yes"][0][0].get<int>();
        } else if (m.contains("yes_bid")) {
          yes_bid = m["yes_bid"].get<int>();
        }
        if (m.contains("no") && m["no"].is_array() && !m["no"].empty() && m["no"][0].is_array() &&
            !m["no"][0].empty()) {
          no_bid = m["no"][0][0].get<int>();
        } else if (m.contains("no_bid")) {
          no_bid = m["no_bid"].get<int>();
        }

        if (yes_bid >= 0) book->yes_bid.store(yes_bid, std::memory_order_relaxed);
        if (no_bid >= 0) book->no_bid.store(no_bid, std::memory_order_relaxed);
        if (m.contains("last_price")) {
          book->last_price.store(m.value("last_price", 0), std::memory_order_relaxed);
        }

        int touched[HotBook::kMaxTickers];
        int touched_n = 0;
        books.sets_touching(tidx, touched, &touched_n);
        recompute_sets(touched, touched_n, t0);
      } catch (...) {
        return;
      }
    });

    ws.connect(conn);
    std::thread ws_thread([&]() { ws.run(); });

    const int64_t session_start_ms = wall_ms();
    bool ever_connected = false;
    while (true) {
      std::this_thread::sleep_for(std::chrono::seconds(1));
      const auto t = wall_ms();

      if (ws_connected.load()) ever_connected = true;
      if (!ws_connected.load()) {
        if (ever_connected || t - session_start_ms > 10000) break;
      }

      if ((t - last_ws_ms.load()) / 1000 >= config::REST_POLL_SECONDS) {
        refresh_cache();
        recompute_all(0);
        last_ws_ms.store(t);
      }

      if ((t - last_order_poll_ms.load()) / 1000 >= config::ORDER_POLL_SECONDS) {
        execution.poll_open_orders();
        last_order_poll_ms.store(t);
      }

      if ((t - last_refresh_ms.load()) / 1000 >= config::MARKET_REFRESH_SECONDS) {
        auto refreshed = find_and_setup_markets(http, key_id, private_key_path, false);
        if (refreshed) {
          books.reset(refreshed->market_sets);
          if (refreshed->date_str != date_str) {
            date_str = refreshed->date_str;
            csv_filename = refreshed->csv_filename;
            init_profit_csv(csv_filename, static_cast<int>(books.set_count()));
          }
          refresh_cache();
          recompute_all(0);
        }
        last_refresh_ms.store(t);
      }
    }

    try {
      ws.stop();
    } catch (...) {
    }
    if (ws_thread.joinable()) ws_thread.join();

    std::cerr << "Reconnecting WebSocket in " << reconnect_delay_ms << "ms...\n";
    refresh_cache();
    recompute_all(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(reconnect_delay_ms));
    reconnect_delay_ms = std::min(reconnect_delay_ms * 2, config::WS_RECONNECT_MAX_MS);
  }
}
