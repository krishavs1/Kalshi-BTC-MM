#include "execution_engine.hpp"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <sstream>
#include <utility>

#include "config.hpp"

namespace {
const char* state_name(OrderLifecycleState state) {
  switch (state) {
    case OrderLifecycleState::Idle:
      return "Idle";
    case OrderLifecycleState::PendingSubmit:
      return "PendingSubmit";
    case OrderLifecycleState::Working:
      return "Working";
    case OrderLifecycleState::PartialFilled:
      return "PartialFilled";
    case OrderLifecycleState::PendingCancel:
      return "PendingCancel";
    case OrderLifecycleState::PendingReplace:
      return "PendingReplace";
    case OrderLifecycleState::Filled:
      return "Filled";
    case OrderLifecycleState::Rejected:
      return "Rejected";
    case OrderLifecycleState::Cancelled:
      return "Cancelled";
  }
  return "Unknown";
}

int clamp_price_cents(int px) { return std::max(1, std::min(99, px)); }
}  // namespace

OrderStateMachine::OrderStateMachine(std::string set_id) : set_id_(std::move(set_id)) {}

ExecutionDecision OrderStateMachine::on_signal(const SyntheticSignal& signal, bool allow_new_orders,
                                               bool needs_replace) {
  ExecutionDecision decision;

  if (state_ == OrderLifecycleState::Filled || state_ == OrderLifecycleState::Rejected ||
      state_ == OrderLifecycleState::Cancelled) {
    state_ = OrderLifecycleState::Idle;
  }

  if (signal.actionable && allow_new_orders && state_ == OrderLifecycleState::Idle) {
    state_ = OrderLifecycleState::PendingSubmit;
    decision.should_submit = true;
    decision.reason = "edge_above_threshold";
    return decision;
  }

  if (signal.actionable && needs_replace &&
      (state_ == OrderLifecycleState::Working || state_ == OrderLifecycleState::PartialFilled)) {
    state_ = OrderLifecycleState::PendingReplace;
    decision.should_replace = true;
    decision.reason = "cancel_replace_price";
    return decision;
  }

  if (!signal.actionable &&
      (state_ == OrderLifecycleState::PendingSubmit || state_ == OrderLifecycleState::Working ||
       state_ == OrderLifecycleState::PartialFilled || state_ == OrderLifecycleState::PendingReplace)) {
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

void OrderStateMachine::on_partial_fill() {
  if (state_ == OrderLifecycleState::Working || state_ == OrderLifecycleState::PendingReplace) {
    state_ = OrderLifecycleState::PartialFilled;
  }
}

void OrderStateMachine::on_fill() {
  if (state_ == OrderLifecycleState::Working || state_ == OrderLifecycleState::PartialFilled ||
      state_ == OrderLifecycleState::PendingCancel || state_ == OrderLifecycleState::PendingReplace) {
    state_ = OrderLifecycleState::Filled;
  }
}

void OrderStateMachine::on_cancel_ack() {
  if (state_ == OrderLifecycleState::PendingCancel || state_ == OrderLifecycleState::Working ||
      state_ == OrderLifecycleState::PartialFilled || state_ == OrderLifecycleState::PendingReplace) {
    state_ = OrderLifecycleState::Cancelled;
  }
}

void OrderStateMachine::on_replace_ack(bool accepted) {
  if (state_ != OrderLifecycleState::PendingReplace) {
    return;
  }
  state_ = accepted ? OrderLifecycleState::Working : OrderLifecycleState::Working;
}

void OrderStateMachine::reset_to_idle() { state_ = OrderLifecycleState::Idle; }

ExecutionEngine::ExecutionEngine(RiskConfig config, OrderClient* order_client)
    : config_(config), orders_(order_client) {}

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

  const double blended_fee_bps = (2.0 * config_.taker_fee_bps) + config_.maker_fee_bps;
  signal.net_edge_cents = signal.gross_edge_cents - blended_fee_bps;
  signal.actionable = signal.net_edge_cents >= config_.min_net_edge_cents;
  return signal;
}

std::vector<LegIntent> ExecutionEngine::build_legs(const MarketSet& markets, const Orderbook& range_ob,
                                                   const Orderbook& lower_ob, const Orderbook& higher_ob,
                                                   SignalSide side, int size) const {
  std::vector<LegIntent> legs;
  if (side == SignalSide::RangeYesLowerYesHigherNo) {
    legs.push_back(LegIntent{markets.range_ticker, BookSide::Bid,
                             clamp_price_cents(range_ob.yes_ask - 1), size, true});
    legs.push_back(LegIntent{markets.lower_leg_ticker, BookSide::Bid,
                             clamp_price_cents(lower_ob.yes_ask), size, false});
    legs.push_back(LegIntent{markets.higher_leg_ticker, BookSide::Ask,
                             clamp_price_cents(100 - higher_ob.no_ask), size, false});
  } else if (side == SignalSide::RangeNoLowerNoHigherYes) {
    legs.push_back(LegIntent{markets.range_ticker, BookSide::Ask,
                             clamp_price_cents(100 - (range_ob.no_ask - 1)), size, true});
    legs.push_back(LegIntent{markets.lower_leg_ticker, BookSide::Ask,
                             clamp_price_cents(100 - lower_ob.no_ask), size, false});
    legs.push_back(LegIntent{markets.higher_leg_ticker, BookSide::Bid,
                             clamp_price_cents(higher_ob.yes_ask), size, false});
  }
  return legs;
}

std::string ExecutionEngine::make_client_order_id(const std::string& set_id, int leg_idx,
                                                  int64_t now_us) {
  std::ostringstream oss;
  oss << set_id << "-L" << leg_idx << "-" << now_us;
  std::string id = oss.str();
  if (id.size() > 64) {
    id.resize(64);
  }
  return id;
}

bool ExecutionEngine::submit_set(WorkingSetState& ws, const std::vector<LegIntent>& legs,
                                 int64_t now_us) {
  ws.orders.clear();
  ws.last_action_ts_us = now_us;

  if (config_.paper_mode || orders_ == nullptr) {
    for (size_t i = 0; i < legs.size(); ++i) {
      RestingOrder o;
      o.order_id = "paper-" + ws.set_id + "-" + std::to_string(i);
      o.client_order_id = make_client_order_id(ws.set_id, static_cast<int>(i), now_us);
      o.ticker = legs[i].ticker;
      o.side = legs[i].side;
      o.price_cents = legs[i].price_cents;
      o.initial_count = legs[i].count;
      o.fill_count = 0.0;
      o.remaining_count = static_cast<double>(legs[i].count);
      o.is_range_leg = legs[i].is_range_leg;
      ws.orders.push_back(o);
      order_to_set_[o.order_id] = ws.set_id;
      std::cout << "[paper] submit " << ws.set_id << " " << o.ticker
                << " side=" << (o.side == BookSide::Bid ? "bid" : "ask")
                << " px=" << o.price_cents << "c qty=" << o.initial_count << "\n";
    }
    return true;
  }

  std::vector<OrderSubmitRequest> reqs;
  reqs.reserve(legs.size());
  for (size_t i = 0; i < legs.size(); ++i) {
    OrderSubmitRequest req;
    req.ticker = legs[i].ticker;
    req.side = legs[i].side;
    req.price_cents = legs[i].price_cents;
    req.count = legs[i].count;
    req.client_order_id = make_client_order_id(ws.set_id, static_cast<int>(i), now_us);
    reqs.push_back(req);
  }

  auto results = orders_->create_orders(reqs);
  bool any_ok = false;
  for (size_t i = 0; i < results.size(); ++i) {
    const auto& r = results[i];
    if (!r.ok || r.order_id.empty()) {
      std::cout << "[live] submit FAIL " << ws.set_id << " leg=" << i << " err=" << r.error
                << " status=" << r.status << "\n";
      continue;
    }
    RestingOrder o;
    o.order_id = r.order_id;
    o.client_order_id = r.client_order_id;
    o.ticker = legs[i].ticker;
    o.side = legs[i].side;
    o.price_cents = legs[i].price_cents;
    o.initial_count = legs[i].count;
    o.fill_count = r.fill_count;
    o.remaining_count = r.remaining_count > 0 ? r.remaining_count
                                              : static_cast<double>(legs[i].count) - r.fill_count;
    o.is_range_leg = legs[i].is_range_leg;
    o.terminal = o.remaining_count <= 1e-9;
    ws.orders.push_back(o);
    order_to_set_[o.order_id] = ws.set_id;
    any_ok = true;
    std::cout << "[live] submit OK " << ws.set_id << " " << o.ticker << " order_id=" << o.order_id
              << " px=" << o.price_cents << "c fill=" << o.fill_count << "/" << o.initial_count
              << "\n";
  }

  if (!any_ok) {
    return false;
  }

  // If only some legs landed, cancel survivors and reject the set to avoid naked risk.
  if (ws.orders.size() != legs.size()) {
    std::cout << "[live] partial-leg submit on " << ws.set_id << " — cancelling survivors\n";
    cancel_set(ws);
    return false;
  }
  return true;
}

bool ExecutionEngine::cancel_set(WorkingSetState& ws) {
  bool all_ok = true;
  if (config_.paper_mode || orders_ == nullptr) {
    for (auto& o : ws.orders) {
      std::cout << "[paper] cancel " << ws.set_id << " " << o.ticker << "\n";
      o.remaining_count = 0;
      o.terminal = true;
    }
    return true;
  }

  for (auto& o : ws.orders) {
    if (o.terminal || o.order_id.empty()) {
      continue;
    }
    auto r = orders_->cancel_order(o.order_id, o.ticker);
    if (!r.ok) {
      all_ok = false;
      std::cout << "[live] cancel FAIL " << ws.set_id << " " << o.order_id << " err=" << r.error
                << "\n";
      continue;
    }
    std::cout << "[live] cancel OK " << ws.set_id << " " << o.order_id
              << " reduced_by=" << r.reduced_by << "\n";
    o.remaining_count = 0;
    o.terminal = true;
  }
  return all_ok;
}

bool ExecutionEngine::replace_range(WorkingSetState& ws, const LegIntent& new_range_leg) {
  RestingOrder* range = nullptr;
  for (auto& o : ws.orders) {
    if (o.is_range_leg && !o.terminal) {
      range = &o;
      break;
    }
  }
  if (!range) {
    return false;
  }

  if (config_.paper_mode || orders_ == nullptr) {
    std::cout << "[paper] replace " << ws.set_id << " " << range->ticker
              << " px=" << range->price_cents << " -> " << new_range_leg.price_cents << "\n";
    range->price_cents = new_range_leg.price_cents;
    ws.target_range_price_cents = new_range_leg.price_cents;
    return true;
  }

  OrderSubmitRequest req;
  req.ticker = new_range_leg.ticker;
  req.side = new_range_leg.side;
  req.price_cents = new_range_leg.price_cents;
  req.count = new_range_leg.count;
  req.client_order_id = range->client_order_id;

  auto r = orders_->amend_order(range->order_id, req);
  if (!r.ok) {
    std::cout << "[live] amend FAIL " << ws.set_id << " " << range->order_id << " err=" << r.error
              << " — falling back to cancel/replace\n";
    // Cancel/replace fallback: cancel range, resubmit range only if other legs still working.
    auto cancel = orders_->cancel_order(range->order_id, range->ticker);
    if (!cancel.ok) {
      return false;
    }
    range->terminal = true;
    OrderSubmitRequest create = req;
    create.client_order_id = make_client_order_id(ws.set_id, 0, ws.last_action_ts_us + 1);
    auto created = orders_->create_order(create);
    if (!created.ok) {
      return false;
    }
    order_to_set_.erase(range->order_id);
    range->order_id = created.order_id;
    range->client_order_id = created.client_order_id;
    range->price_cents = new_range_leg.price_cents;
    range->fill_count = created.fill_count;
    range->remaining_count = created.remaining_count;
    range->terminal = range->remaining_count <= 1e-9;
    order_to_set_[range->order_id] = ws.set_id;
    ws.target_range_price_cents = new_range_leg.price_cents;
    std::cout << "[live] cancel/replace OK " << ws.set_id << " new_order=" << range->order_id
              << " px=" << range->price_cents << "c\n";
    return true;
  }

  range->price_cents = new_range_leg.price_cents;
  range->fill_count = r.fill_count;
  range->remaining_count = r.remaining_count;
  range->terminal = range->remaining_count <= 1e-9;
  ws.target_range_price_cents = new_range_leg.price_cents;
  std::cout << "[live] amend OK " << ws.set_id << " " << range->order_id
            << " px=" << range->price_cents << "c\n";
  return true;
}

void ExecutionEngine::refresh_set_lifecycle(WorkingSetState& ws) {
  if (ws.orders.empty()) {
    return;
  }
  bool any_open = false;
  bool any_fill = false;
  bool all_filled = true;
  for (const auto& o : ws.orders) {
    if (o.fill_count > 1e-9) {
      any_fill = true;
    }
    if (!o.terminal && o.remaining_count > 1e-9) {
      any_open = true;
      all_filled = false;
    } else if (o.fill_count + 1e-9 < static_cast<double>(o.initial_count)) {
      all_filled = false;
    }
  }

  auto sm = state_machines_.find(ws.set_id);
  if (sm == state_machines_.end()) {
    return;
  }

  if (all_filled) {
    sm->second.on_fill();
    ws.state = OrderLifecycleState::Filled;
  } else if (any_fill && any_open) {
    sm->second.on_partial_fill();
    ws.state = OrderLifecycleState::PartialFilled;
  } else if (!any_open && any_fill) {
    // Some filled, remainder cancelled — treat as cancelled with residual inventory warning.
    sm->second.on_cancel_ack();
    ws.state = OrderLifecycleState::Cancelled;
    std::cout << "[warn] " << ws.set_id
              << " closed with partial fills remaining unmatched — check inventory\n";
  }
}

void ExecutionEngine::evaluate_set(const std::string& set_id, const MarketSet& markets,
                                   const ProfitPair& profits, const Orderbook& range_ob,
                                   const Orderbook& lower_ob, const Orderbook& higher_ob,
                                   int64_t now_us) {
  std::lock_guard<std::mutex> lock(mu_);

  auto sm_it = state_machines_.find(set_id);
  if (sm_it == state_machines_.end()) {
    sm_it = state_machines_.emplace(set_id, OrderStateMachine(set_id)).first;
  }
  auto ws_it = working_.find(set_id);
  if (ws_it == working_.end()) {
    WorkingSetState ws;
    ws.set_id = set_id;
    ws_it = working_.emplace(set_id, std::move(ws)).first;
  }

  auto& sm = sm_it->second;
  auto& ws = ws_it->second;

  const SyntheticSignal signal = build_signal(profits, now_us);
  const int open = open_positions();
  const bool allow_new = open < config_.max_open_positions;

  bool needs_replace = false;
  std::vector<LegIntent> desired_legs;
  if (signal.actionable && signal.side != SignalSide::None) {
    desired_legs = build_legs(markets, range_ob, lower_ob, higher_ob, signal.side, config_.order_size);
    if (!desired_legs.empty()) {
      const int desired_range_px = desired_legs.front().price_cents;
      if ((sm.state() == OrderLifecycleState::Working ||
           sm.state() == OrderLifecycleState::PartialFilled) &&
          ws.side == signal.side &&
          std::abs(desired_range_px - ws.target_range_price_cents) >= config_.replace_min_tick_change) {
        needs_replace = true;
      }
    }
  }

  ExecutionDecision decision = sm.on_signal(signal, allow_new, needs_replace);

  if (decision.should_submit) {
    if (desired_legs.empty()) {
      desired_legs = build_legs(markets, range_ob, lower_ob, higher_ob, signal.side, config_.order_size);
    }
    ws.side = signal.side;
    ws.target_range_price_cents = desired_legs.empty() ? 0 : desired_legs.front().price_cents;
    const bool ok = submit_set(ws, desired_legs, now_us);
    sm.on_submit_ack(ok);
    ws.state = sm.state();
    if (ok) {
      refresh_set_lifecycle(ws);
      std::cout << "[exec] " << set_id << " -> " << state_name(sm.state())
                << " net=" << signal.net_edge_cents << "c\n";
    }
  }

  if (decision.should_replace && !desired_legs.empty()) {
    const bool ok = replace_range(ws, desired_legs.front());
    sm.on_replace_ack(ok);
    ws.state = sm.state();
    refresh_set_lifecycle(ws);
  }

  if (decision.should_cancel) {
    const bool ok = cancel_set(ws);
    if (ok) {
      sm.on_cancel_ack();
    }
    ws.state = sm.state();
    std::cout << "[exec] cancel " << set_id << " reason=" << decision.reason
              << " state=" << state_name(sm.state()) << "\n";
  }
}

void ExecutionEngine::on_fill_update(const std::string& order_id, double fill_count,
                                     bool has_remaining, double remaining_count) {
  std::lock_guard<std::mutex> lock(mu_);
  auto map_it = order_to_set_.find(order_id);
  if (map_it == order_to_set_.end()) {
    return;
  }
  auto ws_it = working_.find(map_it->second);
  if (ws_it == working_.end()) {
    return;
  }
  auto& ws = ws_it->second;
  for (auto& o : ws.orders) {
    if (o.order_id != order_id) {
      continue;
    }
    if (has_remaining) {
      o.remaining_count = remaining_count;
      o.fill_count = std::max(o.fill_count, static_cast<double>(o.initial_count) - remaining_count);
    } else {
      o.fill_count += fill_count;
      o.remaining_count = std::max(0.0, static_cast<double>(o.initial_count) - o.fill_count);
    }
    if (o.remaining_count <= 1e-9 || o.fill_count + 1e-9 >= static_cast<double>(o.initial_count)) {
      o.terminal = true;
      o.remaining_count = 0;
      o.fill_count = static_cast<double>(o.initial_count);
    }
    std::cout << "[fill] " << ws.set_id << " " << o.ticker << " filled=" << o.fill_count
              << " remaining=" << o.remaining_count << "\n";
    break;
  }
  refresh_set_lifecycle(ws);
}

void ExecutionEngine::poll_open_orders() {
  if (config_.paper_mode || orders_ == nullptr) {
    return;
  }
  std::lock_guard<std::mutex> lock(mu_);
  for (auto& [set_id, ws] : working_) {
    if (ws.state != OrderLifecycleState::Working && ws.state != OrderLifecycleState::PartialFilled &&
        ws.state != OrderLifecycleState::PendingCancel &&
        ws.state != OrderLifecycleState::PendingReplace) {
      continue;
    }
    for (auto& o : ws.orders) {
      if (o.terminal || o.order_id.empty()) {
        continue;
      }
      auto st = orders_->get_order(o.order_id);
      if (!st.ok) {
        continue;
      }
      o.fill_count = st.fill_count;
      o.remaining_count = st.remaining_count;
      if (st.order_status == "executed" || st.remaining_count <= 1e-9) {
        o.terminal = true;
        o.remaining_count = 0;
      } else if (st.order_status == "canceled") {
        o.terminal = true;
        o.remaining_count = 0;
      }
    }
    refresh_set_lifecycle(ws);
  }
}

int ExecutionEngine::open_positions() const {
  int n = 0;
  for (const auto& [_, ws] : working_) {
    if (ws.state == OrderLifecycleState::Working || ws.state == OrderLifecycleState::PartialFilled ||
        ws.state == OrderLifecycleState::PendingSubmit ||
        ws.state == OrderLifecycleState::PendingReplace ||
        ws.state == OrderLifecycleState::PendingCancel) {
      ++n;
    }
  }
  return n;
}
