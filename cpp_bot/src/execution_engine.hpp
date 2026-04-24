#pragma once

#include <string>
#include <unordered_map>

#include "models.hpp"

struct RiskConfig {
  double maker_fee_bps{0.0};
  double taker_fee_bps{0.0};
  double min_net_edge_cents{0.0};
  int max_open_positions{1};
  bool paper_mode{true};
};

class OrderStateMachine {
 public:
  explicit OrderStateMachine(std::string set_id);

  ExecutionDecision on_signal(const SyntheticSignal& signal, bool allow_new_orders);
  void on_submit_ack(bool accepted);
  void on_fill();
  void on_cancel_ack();
  OrderLifecycleState state() const { return state_; }

 private:
  std::string set_id_;
  OrderLifecycleState state_{OrderLifecycleState::Idle};
};

class ExecutionEngine {
 public:
  explicit ExecutionEngine(RiskConfig config);

  SyntheticSignal build_signal(const ProfitPair& profits, int64_t now_us) const;
  void evaluate_set(const std::string& set_id, const ProfitPair& profits, int64_t now_us);

 private:
  RiskConfig config_;
  std::unordered_map<std::string, OrderStateMachine> state_machines_;
  int open_positions_{0};
};
