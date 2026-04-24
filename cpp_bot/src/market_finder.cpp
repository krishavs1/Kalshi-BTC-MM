#include "market_finder.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <sstream>

#include "auth.hpp"
#include "config.hpp"
#include "profit_calculator.hpp"

namespace {
struct DateInfo {
  std::string date_str;
};

DateInfo get_current_est_hour() {
  using namespace std::chrono;
  const auto now_utc = system_clock::now();
  const auto est = now_utc - hours(5);
  std::time_t t = system_clock::to_time_t(est);
  std::tm tm = *std::gmtime(&t);
  const int next_hour = (tm.tm_hour + 1) % 24;
  if (next_hour < tm.tm_hour) {
    tm.tm_mday += 1;
    std::mktime(&tm);
  }

  std::ostringstream os;
  os << "26JAN" << std::setw(2) << std::setfill('0') << tm.tm_mday << std::setw(2) << next_hour;
  return {os.str()};
}

std::vector<nlohmann::json> find_range_markets(HttpClient& http, const std::string& key_id,
                                               const std::string& private_key_path,
                                               const std::string& date_str) {
  std::vector<nlohmann::json> result;
  auto headers = get_auth_headers(key_id, private_key_path, "GET", "/trade-api/v2/markets");

  std::vector<std::string> urls = {
      std::string(config::REST_URL_BASE) + "?limit=1000&event_ticker=KXBTC-" + date_str,
      std::string(config::REST_URL_BASE) + "?limit=1000&series_ticker=KXBT",
      std::string(config::REST_URL_BASE) + "?limit=2000&series_ticker=KXBTC",
  };

  for (const auto& url : urls) {
    auto json = http.get_json(url, headers, 2000);
    if (!json || !json->contains("markets") || !(*json)["markets"].is_array()) {
      continue;
    }
    for (const auto& m : (*json)["markets"]) {
      const std::string ticker = m.value("ticker", "");
      if (ticker.find("KXBTC-") == 0 && ticker.find(date_str) != std::string::npos &&
          ticker.find("-B") != std::string::npos && m.value("market_type", "") == "binary" &&
          m.value("strike_type", "") == "between") {
        result.push_back(m);
      }
    }
    if (!result.empty()) {
      return result;
    }
  }
  return {};
}

double score_market(const nlohmann::json& m) {
  const double yes_bid = m.value("yes_bid", 0);
  const double yes_ask = m.value("yes_ask", 100);
  const double no_bid = m.value("no_bid", 0);
  const double no_ask = m.value("no_ask", 100);
  const double yes_spread = yes_ask > yes_bid ? yes_ask - yes_bid : 100;
  const double no_spread = no_ask > no_bid ? no_ask - no_bid : 100;
  const double spread = std::min(yes_spread, no_spread);
  const double notional = std::stod(m.value("notional_value_dollars", std::string("0")));
  const double oi = m.value("open_interest", 0);
  const double vol24 = m.value("volume_24h", 0);
  const double liquidity = 1 + (notional / 100) + (oi / 1000) + (vol24 / 10000);
  return spread >= 100 ? liquidity * 10 : (100 - spread) * liquidity;
}

std::pair<std::optional<nlohmann::json>, std::optional<nlohmann::json>> find_over_markets(
    HttpClient& http, const std::string& key_id, const std::string& private_key_path,
    const nlohmann::json& range_market, const std::string& date_str) {
  const auto floor = range_market.value("floor_strike", 0.0);
  const auto cap = range_market.value("cap_strike", 0.0);
  if (floor <= 0 || cap <= 0) {
    return {std::nullopt, std::nullopt};
  }

  auto headers = get_auth_headers(key_id, private_key_path, "GET", "/trade-api/v2/markets");
  auto json = http.get_json(std::string(config::REST_URL_BASE) + "?limit=1000&event_ticker=KXBTCD-" +
                                date_str,
                            headers, 2000);
  if (!json || !json->contains("markets")) {
    return {std::nullopt, std::nullopt};
  }

  std::vector<nlohmann::json> greater;
  for (const auto& m : (*json)["markets"]) {
    if (m.value("strike_type", "") == "greater" && m.value("market_type", "") == "binary" &&
        !m["floor_strike"].is_null()) {
      greater.push_back(m);
    }
  }
  if (greater.empty()) {
    return {std::nullopt, std::nullopt};
  }

  const double lower_target = floor - 0.01;
  auto lower_it = std::min_element(greater.begin(), greater.end(), [&](const auto& a, const auto& b) {
    return std::abs(a.value("floor_strike", 1e12) - lower_target) <
           std::abs(b.value("floor_strike", 1e12) - lower_target);
  });
  auto cap_it = std::min_element(greater.begin(), greater.end(), [&](const auto& a, const auto& b) {
    return std::abs(a.value("floor_strike", 1e12) - cap) < std::abs(b.value("floor_strike", 1e12) - cap);
  });

  std::optional<nlohmann::json> lower, upper;
  if (lower_it != greater.end() &&
      std::abs(lower_it->value("floor_strike", 1e12) - lower_target) <= 250.0) {
    lower = *lower_it;
  }
  if (cap_it != greater.end() && std::abs(cap_it->value("floor_strike", 1e12) - cap) <= 1.0) {
    upper = *cap_it;
  }
  return {lower, upper};
}
}  // namespace

std::optional<MarketDiscoveryResult> find_and_setup_markets(HttpClient& http, const std::string& key_id,
                                                            const std::string& private_key_path,
                                                            bool init_csv) {
  const auto date_info = get_current_est_hour();
  const auto ranges = find_range_markets(http, key_id, private_key_path, date_info.date_str);
  if (ranges.empty()) {
    return std::nullopt;
  }

  std::vector<nlohmann::json> sorted = ranges;
  std::sort(sorted.begin(), sorted.end(), [](const auto& a, const auto& b) {
    return score_market(a) > score_market(b);
  });
  if (sorted.size() > static_cast<size_t>(config::TOP_RANGE_MARKETS)) {
    sorted.resize(config::TOP_RANGE_MARKETS);
  }

  std::vector<MarketSet> sets;
  for (const auto& range : sorted) {
    auto [lower, upper] = find_over_markets(http, key_id, private_key_path, range, date_info.date_str);
    if (lower && upper) {
      sets.push_back(MarketSet{
          range.value("ticker", ""),
          lower->value("ticker", ""),
          upper->value("ticker", ""),
      });
    }
  }
  if (sets.empty()) {
    return std::nullopt;
  }

  MarketDiscoveryResult out;
  out.market_sets = std::move(sets);
  out.date_str = date_info.date_str;
  out.csv_filename = "data/profits_" + out.date_str + ".csv";
  if (init_csv) {
    init_profit_csv(out.csv_filename, static_cast<int>(out.market_sets.size()));
  }
  return out;
}
