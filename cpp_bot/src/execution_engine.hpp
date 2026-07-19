#pragma once

#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#include "latency.hpp"
#include "models.hpp"
#include "order_client.hpp"

struct RiskConfig {
  double maker_fee_bps{0.0};
  double taker_fee_bps{0.0};
  double min_net_edge_cents{0.0};
  int max_open_positions{1};
  bool paper_mode{true};
  int order_size{1};
  int replace_min_tick_change{1};
};

struct WorkingSetState {
  std::string set_id;
  OrderLifecycleState state{OrderLifecycleState::Idle};
  SignalSide side{SignalSide::None};
  std::vector<RestingOrder> orders;
  int target_range_price_cents{0};
  int64_t last_action_ts_us{0};
};

class OrderStateMachine {
 public:
  explicit OrderStateMachine(std::string set_id);

  ExecutionDecision on_signal(const SyntheticSignal& signal, bool allow_new_orders, bool needs_replace);
  void on_submit_ack(bool accepted);
  void on_partial_fill();
  void on_fill();
  void on_cancel_ack();
  void on_replace_ack(bool accepted);
  void reset_to_idle();
  OrderLifecycleState state() const { return state_; }
  void set_state(OrderLifecycleState state) { state_ = state; }

 private:
  std::string set_id_;
  OrderLifecycleState state_{OrderLifecycleState::Idle};
};

class ExecutionEngine {
 public:
  ExecutionEngine(RiskConfig config, OrderClient* order_client, LatencyStats* decide_stats,
                  LatencyStats* submit_stats);

  SyntheticSignal build_signal(const ProfitPair& profits, int64_t now_us) const;
  std::vector<LegIntent> build_legs(const MarketSet& markets, const Orderbook& range_ob,
                                    const Orderbook& lower_ob, const Orderbook& higher_ob,
                                    SignalSide side, int size) const;

  // ws_recv_ns: steady_clock timestamp when the triggering WS frame was received (0 if N/A).
  void evaluate_set(const std::string& set_id, const MarketSet& markets, const ProfitPair& profits,
                    const Orderbook& range_ob, const Orderbook& lower_ob, const Orderbook& higher_ob,
                    int64_t now_us, int64_t ws_recv_ns = 0);

  void on_fill_update(const std::string& order_id, double fill_count, bool has_remaining,
                      double remaining_count);
  void poll_open_orders();
  int open_positions() const;

 private:
  bool submit_set(WorkingSetState& ws, const std::vector<LegIntent>& legs, int64_t now_us,
                  int64_t ws_recv_ns);
  bool cancel_set(WorkingSetState& ws);
  bool replace_range(WorkingSetState& ws, const LegIntent& new_range_leg);
  void refresh_set_lifecycle(WorkingSetState& ws);
  static std::string make_client_order_id(const std::string& set_id, int leg_idx, int64_t now_us);

  RiskConfig config_;
  OrderClient* orders_;
  LatencyStats* decide_stats_;
  LatencyStats* submit_stats_;
  std::unordered_map<std::string, OrderStateMachine> state_machines_;
  std::unordered_map<std::string, WorkingSetState> working_;
  std::unordered_map<std::string, std::string> order_to_set_;
  mutable std::mutex mu_;
};
