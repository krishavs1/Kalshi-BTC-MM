#include "smart_monitor.hpp"

#include <chrono>
#include <iostream>
#include <mutex>
#include <set>
#include <thread>
#include <unordered_map>

#include <nlohmann/json.hpp>
#include <websocketpp/client.hpp>
#include <websocketpp/config/asio_client.hpp>

#include "auth.hpp"
#include "config.hpp"
#include "http_client.hpp"
#include "market_finder.hpp"
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
}  // namespace

void run_monitor(const std::string& key_id, const std::string& private_key_path) {
  HttpClient http;

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

  auto refresh_cache = [&]() {
    std::set<std::string> tickers;
    for (const auto& s : market_sets) {
      tickers.insert(s.range_ticker);
      tickers.insert(s.lower_leg_ticker);
      tickers.insert(s.higher_leg_ticker);
    }
    std::lock_guard<std::mutex> lock(cache_mu);
    for (const auto& t : tickers) {
      cache[t] = rest_orderbook(http, key_id, private_key_path, t);
    }
  };

  auto recompute = [&]() {
    std::vector<ProfitPair> profits;
    bool ready = false;
    {
      std::lock_guard<std::mutex> lock(cache_mu);
      for (const auto& s : market_sets) {
        auto r = cache.find(s.range_ticker);
        auto l = cache.find(s.lower_leg_ticker);
        auto h = cache.find(s.higher_leg_ticker);
        const Orderbook* ro = r == cache.end() ? nullptr : &r->second;
        const Orderbook* lo = l == cache.end() ? nullptr : &l->second;
        const Orderbook* ho = h == cache.end() ? nullptr : &h->second;
        auto p = calculate_profits(ro, lo, ho);
        if (p.valid &&
            (p.profit1 > config::PROFIT_THRESHOLD_CENTS || p.profit2 > config::PROFIT_THRESHOLD_CENTS)) {
          ready = true;
        }
        profits.push_back(p);
      }
    }
    if (ready) {
      std::cout << "PROFIT > " << config::PROFIT_THRESHOLD_CENTS << "c - READY FOR TRADING\n";
    }
    log_profits_to_csv(csv_filename, profits);
  };

  refresh_cache();
  recompute();

  WsClient ws;
  ws.clear_access_channels(websocketpp::log::alevel::all);
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
    throw std::runtime_error("WebSocket connection setup failed");
  }
  for (const auto& [k, v] : ws_headers) {
    conn->append_header(k, v);
  }

  std::atomic<int64_t> last_ws_ms{
      std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch())
          .count()};
  std::atomic<int64_t> last_refresh_ms{last_ws_ms.load()};

  ws.set_open_handler([&](websocketpp::connection_hdl hdl) {
    std::set<std::string> tickers;
    for (const auto& s : market_sets) {
      tickers.insert(s.range_ticker);
      tickers.insert(s.lower_leg_ticker);
      tickers.insert(s.higher_leg_ticker);
    }
    nlohmann::json sub{
        {"id", 1},
        {"cmd", "subscribe"},
        {"params", {{"channels", {"orderbook_delta"}}, {"market_tickers", tickers}}},
    };
    ws.send(hdl, sub.dump(), websocketpp::frame::opcode::text);
  });

  ws.set_message_handler([&](websocketpp::connection_hdl, WsClient::message_ptr msg) {
    auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
                   std::chrono::system_clock::now().time_since_epoch())
                   .count();
    last_ws_ms.store(now);

    try {
      auto data = nlohmann::json::parse(msg->get_payload());
      const std::string type = data.value("type", "");
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

      std::lock_guard<std::mutex> lock(cache_mu);
      auto& ob = cache[ticker];
      if (yes_bid >= 0) ob.yes_bid = yes_bid;
      if (no_bid >= 0) ob.no_bid = no_bid;
      ob.yes_ask = 100 - ob.no_bid;
      ob.no_ask = 100 - ob.yes_bid;
      ob.last_price = m.value("last_price", ob.last_price);
    } catch (...) {
      return;
    }
    recompute();
  });

  ws.connect(conn);
  std::thread ws_thread([&]() { ws.run(); });

  while (true) {
    std::this_thread::sleep_for(std::chrono::seconds(1));
    const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                            std::chrono::system_clock::now().time_since_epoch())
                            .count();

    if ((now_ms - last_ws_ms.load()) / 1000 >= config::REST_POLL_SECONDS) {
      refresh_cache();
      recompute();
      last_ws_ms.store(now_ms);
    }

    if ((now_ms - last_refresh_ms.load()) / 1000 >= config::MARKET_REFRESH_SECONDS) {
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
      last_refresh_ms.store(now_ms);
    }
  }

  ws_thread.join();
}
