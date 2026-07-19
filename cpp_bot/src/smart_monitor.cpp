#include "smart_monitor.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <iostream>
#include <mutex>
#include <set>
#include <thread>
#include <unordered_map>
#include <vector>

#include <nlohmann/json.hpp>
#include <websocketpp/client.hpp>
#include <websocketpp/config/asio_client.hpp>

#include "auth.hpp"
#include "config.hpp"
#include "execution_engine.hpp"
#include "http_client.hpp"
#include "market_finder.hpp"
#include "order_client.hpp"
#include "profit_calculator.hpp"

using WsClient = websocketpp::client<websocketpp::config::asio_tls_client>;

namespace {
Orderbook rest_orderbook(HttpClient& http, const std::string& key_id, const std::string& private_key_path,
                         const std::string& ticker) {
  auto headers = get_auth_headers(key_id, private_key_path, "GET", "/trade-api/v2/markets/" + ticker);
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

int64_t now_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

int64_t now_us() {
  return std::chrono::duration_cast<std::chrono::microseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
}  // namespace

void run_monitor(const std::string& key_id, const std::string& private_key_path) {
  HttpClient http;
  OrderClient order_client(http, key_id, private_key_path);

  if (config::ENABLE_PAPER_EXECUTION) {
    std::cout << "Mode: PAPER (no live orders)\n";
  } else {
    std::cout << "Mode: LIVE order submission enabled\n";
  }

  std::optional<MarketDiscoveryResult> discovery;
  while (!discovery) {
    discovery = find_and_setup_markets(http, key_id, private_key_path, true);
    if (!discovery) {
      std::cout << "No markets found. Retrying in 60 seconds...\n";
      std::this_thread::sleep_for(std::chrono::seconds(60));
    }
  }

  std::vector<MarketSet> market_sets = discovery->market_sets;
  std::string csv_filename = discovery->csv_filename;
  std::string date_str = discovery->date_str;

  std::unordered_map<std::string, Orderbook> cache;
  std::mutex cache_mu;

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
      config::ENABLE_PAPER_EXECUTION ? nullptr : &order_client);

  int64_t last_csv_ms = now_ms();
  std::atomic<int64_t> last_ws_ms{now_ms()};
  std::atomic<int64_t> last_refresh_ms{now_ms()};
  std::atomic<int64_t> last_order_poll_ms{now_ms()};
  std::atomic<bool> ws_connected{false};

  auto collect_tickers = [&]() {
    std::set<std::string> tickers;
    for (const auto& s : market_sets) {
      tickers.insert(s.range_ticker);
      tickers.insert(s.lower_leg_ticker);
      tickers.insert(s.higher_leg_ticker);
    }
    return tickers;
  };

  auto refresh_cache = [&]() {
    const auto tickers = collect_tickers();
    std::lock_guard<std::mutex> lock(cache_mu);
    for (const auto& t : tickers) {
      cache[t] = rest_orderbook(http, key_id, private_key_path, t);
    }
  };

  auto recompute = [&]() {
    std::vector<ProfitPair> profits;
    std::vector<std::string> set_ids;
    std::vector<MarketSet> sets_copy;
    std::vector<Orderbook> range_obs;
    std::vector<Orderbook> lower_obs;
    std::vector<Orderbook> higher_obs;
    bool ready = false;

    {
      std::lock_guard<std::mutex> lock(cache_mu);
      for (size_t i = 0; i < market_sets.size(); ++i) {
        const auto& s = market_sets[i];
        auto r = cache.find(s.range_ticker);
        auto l = cache.find(s.lower_leg_ticker);
        auto h = cache.find(s.higher_leg_ticker);
        const Orderbook empty{};
        const Orderbook& ro = r == cache.end() ? empty : r->second;
        const Orderbook& lo = l == cache.end() ? empty : l->second;
        const Orderbook& ho = h == cache.end() ? empty : h->second;
        auto p = calculate_profits(r == cache.end() ? nullptr : &r->second,
                                   l == cache.end() ? nullptr : &l->second,
                                   h == cache.end() ? nullptr : &h->second);
        if (p.valid &&
            (p.profit1 > config::PROFIT_THRESHOLD_CENTS || p.profit2 > config::PROFIT_THRESHOLD_CENTS)) {
          ready = true;
        }
        set_ids.push_back("set_" + std::to_string(i + 1));
        profits.push_back(p);
        sets_copy.push_back(s);
        range_obs.push_back(ro);
        lower_obs.push_back(lo);
        higher_obs.push_back(ho);
      }
    }

    const int64_t decision_ts_us = now_us();
    for (size_t i = 0; i < profits.size(); ++i) {
      execution.evaluate_set(set_ids[i], sets_copy[i], profits[i], range_obs[i], lower_obs[i],
                             higher_obs[i], decision_ts_us);
    }

    if (ready) {
      std::cout << "PROFIT > " << config::PROFIT_THRESHOLD_CENTS << "c - READY FOR TRADING\n";
    }
    const int64_t current_ms = now_ms();
    if ((current_ms - last_csv_ms) >= config::CSV_WRITE_MS) {
      log_profits_to_csv(csv_filename, profits);
      last_csv_ms = current_ms;
    }
  };

  refresh_cache();
  recompute();

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

    auto ws_headers = get_auth_headers(key_id, private_key_path, "GET", "/trade-api/ws/v2");
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
      last_ws_ms.store(now_ms());
      std::cout << "WebSocket connected\n";

      const auto tickers = collect_tickers();
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
      last_ws_ms.store(now_ms());
      try {
        auto data = nlohmann::json::parse(msg->get_payload());
        const std::string type = data.value("type", "");

        if (type == "fill") {
          auto m = data.contains("msg") ? data["msg"] : data;
          const std::string order_id = m.value("order_id", "");
          if (order_id.empty()) {
            return;
          }
          double fill_count = 0.0;
          if (m.contains("count_fp") && m["count_fp"].is_string()) {
            fill_count = std::stod(m["count_fp"].get<std::string>());
          } else if (m.contains("count")) {
            fill_count = m["count"].is_number() ? m["count"].get<double>()
                                                : std::stod(m["count"].get<std::string>());
          } else if (m.contains("count_fp") && m["count_fp"].is_number()) {
            fill_count = m["count_fp"].get<double>();
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
        if (ticker.empty()) {
          return;
        }

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

        {
          std::lock_guard<std::mutex> lock(cache_mu);
          auto& ob = cache[ticker];
          if (yes_bid >= 0) ob.yes_bid = yes_bid;
          if (no_bid >= 0) ob.no_bid = no_bid;
          ob.yes_ask = 100 - ob.no_bid;
          ob.no_ask = 100 - ob.yes_bid;
          ob.last_price = m.value("last_price", ob.last_price);
        }
      } catch (...) {
        return;
      }
      recompute();
    });

    ws.connect(conn);
    std::thread ws_thread([&]() { ws.run(); });

    const int64_t session_start_ms = now_ms();
    bool ever_connected = false;
    while (true) {
      std::this_thread::sleep_for(std::chrono::seconds(1));
      const auto t = now_ms();

      if (ws_connected.load()) {
        ever_connected = true;
      }

      // Give the socket a few seconds to come up; once it has connected, exit when it drops.
      if (!ws_connected.load()) {
        if (ever_connected) {
          break;
        }
        if (t - session_start_ms > 10000) {
          break;
        }
      }

      if ((t - last_ws_ms.load()) / 1000 >= config::REST_POLL_SECONDS) {
        refresh_cache();
        recompute();
        last_ws_ms.store(t);
      }

      if ((t - last_order_poll_ms.load()) / 1000 >= config::ORDER_POLL_SECONDS) {
        execution.poll_open_orders();
        last_order_poll_ms.store(t);
      }

      if ((t - last_refresh_ms.load()) / 1000 >= config::MARKET_REFRESH_SECONDS) {
        auto refreshed = find_and_setup_markets(http, key_id, private_key_path, false);
        if (refreshed) {
          market_sets = refreshed->market_sets;
          if (refreshed->date_str != date_str) {
            date_str = refreshed->date_str;
            csv_filename = refreshed->csv_filename;
            init_profit_csv(csv_filename, static_cast<int>(market_sets.size()));
          }
          refresh_cache();
          recompute();
        }
        last_refresh_ms.store(t);
      }
    }

    try {
      ws.stop();
    } catch (...) {
    }
    if (ws_thread.joinable()) {
      ws_thread.join();
    }

    std::cerr << "Reconnecting WebSocket in " << reconnect_delay_ms << "ms...\n";
    refresh_cache();
    recompute();
    std::this_thread::sleep_for(std::chrono::milliseconds(reconnect_delay_ms));
    reconnect_delay_ms = std::min(reconnect_delay_ms * 2, config::WS_RECONNECT_MAX_MS);
  }
}
