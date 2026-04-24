#pragma once

#include <cstdint>
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

enum class SignalSide {
  None = 0,
  RangeYesLowerYesHigherNo,
  RangeNoLowerNoHigherYes,
};

struct SyntheticSignal {
  bool actionable{false};
  SignalSide side{SignalSide::None};
  double gross_edge_cents{0.0};
  double net_edge_cents{0.0};
  int64_t decision_ts_us{0};
};

enum class OrderLifecycleState {
  Idle = 0,
  PendingSubmit,
  Working,
  PendingCancel,
  Filled,
  Rejected,
  Cancelled,
};

struct ExecutionDecision {
  bool should_submit{false};
  bool should_cancel{false};
  std::string reason;
};

using HeaderMap = std::unordered_map<std::string, std::string>;
