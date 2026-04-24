#pragma once

#include <string>
#include <unordered_map>
#include <vector>

struct Orderbook {
  int yes_bid{0};
  int no_bid{0};
  int yes_ask{100};
  int no_ask{100};
  int last_price{0};
};

struct MarketSet {
  std::string range_ticker;
  std::string lower_leg_ticker;
  std::string higher_leg_ticker;
};

struct ProfitPair {
  double profit1{0.0};
  double profit2{0.0};
  bool valid{false};
};

using HeaderMap = std::unordered_map<std::string, std::string>;
