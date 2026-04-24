#include "execution_engine.hpp"

#include <algorithm>
#include <iostream>
#include <utility>

namespace {
const char* state_name(OrderLifecycleState state) {
  switch (state) {
    case OrderLifecycleState::Idle:
      return "Idle";
    case OrderLifecycleState::PendingSubmit:
      return "PendingSubmit";
    case OrderLifecycleState::Working:
      return "Working";
    case OrderLifecycleState::PendingCancel:
      return "PendingCancel";
    case OrderLifecycleState::Filled:
      return "Filled";
    case OrderLifecycleState::Rejected:
      return "Rejected";
    case OrderLifecycleState::Cancelled:
      return "Cancelled";
  }
  return "Unknown";
}
}  // namespace

OrderStateMachine::OrderStateMachine(std::string set_id) : set_id_(std::move(set_id)) {}

ExecutionDecision OrderStateMachine::on_signal(const SyntheticSignal& signal, bool allow_new_orders) {
  ExecutionDecision decision;
  if (signal.actionable && allow_new_orders && state_ == OrderLifecycleState::Idle) {
    state_ = OrderLifecycleState::PendingSubmit;
    decision.should_submit = true;
    decision.reason = "edge_above_threshold";
    return decision;
  }

  if (!signal.actionable &&
      (state_ == OrderLifecycleState::PendingSubmit || state_ == OrderLifecycleState::Working)) {
    state_ = OrderLifecycleState::PendingCancel;
    decision.should_cancel = true;
    decision.reason = "edge_decay_or_risk_guard";
  }
  return decision;
}

void OrderStateMachine::on_submit_ack(bool accepted) {
  if (state_ != OrderLifecycleState::PendingSubmit) {
    return;
  }
  state_ = accepted ? OrderLifecycleState::Working : OrderLifecycleState::Rejected;
}

void OrderStateMachine::on_fill() {
  if (state_ == OrderLifecycleState::Working) {
    state_ = OrderLifecycleState::Filled;
  }
}

void OrderStateMachine::on_cancel_ack() {
  if (state_ == OrderLifecycleState::PendingCancel) {
    state_ = OrderLifecycleState::Cancelled;
  }
}

ExecutionEngine::ExecutionEngine(RiskConfig config) : config_(config) {}

SyntheticSignal ExecutionEngine::build_signal(const ProfitPair& profits, int64_t now_us) const {
  SyntheticSignal signal;
  signal.decision_ts_us = now_us;
  if (!profits.valid) {
    return signal;
  }

  const bool side1_better = profits.profit1 >= profits.profit2;
  signal.side = side1_better ? SignalSide::RangeYesLowerYesHigherNo
                             : SignalSide::RangeNoLowerNoHigherYes;
  signal.gross_edge_cents = std::max(profits.profit1, profits.profit2);

  // Approximate 3-leg synthetic execution fee in cents per $100 notional.
  const double blended_fee_bps = (2.0 * config_.maker_fee_bps) + config_.taker_fee_bps;
  const double fee_cents = blended_fee_bps;
  signal.net_edge_cents = signal.gross_edge_cents - fee_cents;
  signal.actionable = signal.net_edge_cents >= config_.min_net_edge_cents;
  return signal;
}

void ExecutionEngine::evaluate_set(const std::string& set_id, const ProfitPair& profits, int64_t now_us) {
  auto it = state_machines_.find(set_id);
  if (it == state_machines_.end()) {
    it = state_machines_.emplace(set_id, OrderStateMachine(set_id)).first;
  }

  const SyntheticSignal signal = build_signal(profits, now_us);
  const bool allow_new = open_positions_ < config_.max_open_positions;
  ExecutionDecision decision = it->second.on_signal(signal, allow_new);

  if (decision.should_submit) {
    if (config_.paper_mode) {
      std::cout << "[paper] submit " << set_id << " side=" << static_cast<int>(signal.side)
                << " gross=" << signal.gross_edge_cents << "c net=" << signal.net_edge_cents
                << "c ts_us=" << signal.decision_ts_us << "\n";
      it->second.on_submit_ack(true);
      ++open_positions_;
    } else {
      std::cout << "[live] submit TODO " << set_id << "\n";
    }
  }

  if (decision.should_cancel) {
    if (config_.paper_mode) {
      std::cout << "[paper] cancel " << set_id << " reason=" << decision.reason
                << " state=" << state_name(it->second.state()) << "\n";
      it->second.on_cancel_ack();
      if (open_positions_ > 0) {
        --open_positions_;
      }
    } else {
      std::cout << "[live] cancel TODO " << set_id << "\n";
    }
  }
}
