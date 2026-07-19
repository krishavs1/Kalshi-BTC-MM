#pragma once

#include <atomic>
#include <array>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "config.hpp"
#include "models.hpp"

// Fixed-slot orderbook cache for the hot path. Bids are atomics so WS updates
// don't need a mutex for reads during decisioning.
struct AtomicBook {
  std::atomic<int> yes_bid{0};
  std::atomic<int> no_bid{0};
  std::atomic<int> last_price{0};

  Orderbook snapshot() const {
    Orderbook ob;
    ob.yes_bid = yes_bid.load(std::memory_order_relaxed);
    ob.no_bid = no_bid.load(std::memory_order_relaxed);
    ob.yes_ask = 100 - ob.no_bid;
    ob.no_ask = 100 - ob.yes_bid;
    ob.last_price = last_price.load(std::memory_order_relaxed);
    return ob;
  }

  void store(const Orderbook& ob) {
    yes_bid.store(ob.yes_bid, std::memory_order_relaxed);
    no_bid.store(ob.no_bid, std::memory_order_relaxed);
    last_price.store(ob.last_price, std::memory_order_relaxed);
  }
};

class HotBook {
 public:
  static constexpr int kMaxTickers = config::TOP_RANGE_MARKETS * 3 + 8;

  void reset(const std::vector<MarketSet>& sets) {
    ticker_to_idx_.clear();
    set_tickers_.clear();
    n_ = 0;
    sets_ = sets;
    for (size_t si = 0; si < sets_.size(); ++si) {
      const auto& s = sets_[si];
      const int r = ensure(s.range_ticker);
      const int l = ensure(s.lower_leg_ticker);
      const int h = ensure(s.higher_leg_ticker);
      set_tickers_.push_back({r, l, h});
    }
  }

  int index_of(const std::string& ticker) const {
    auto it = ticker_to_idx_.find(ticker);
    return it == ticker_to_idx_.end() ? -1 : it->second;
  }

  AtomicBook* book_at(int idx) {
    if (idx < 0 || idx >= n_) return nullptr;
    return &books_[idx];
  }

  const AtomicBook* book_at(int idx) const {
    if (idx < 0 || idx >= n_) return nullptr;
    return &books_[idx];
  }

  const std::vector<MarketSet>& sets() const { return sets_; }
  int set_count() const { return static_cast<int>(sets_.size()); }

  // Returns set indices that reference this ticker index.
  void sets_touching(int ticker_idx, int* out, int* out_n) const {
    *out_n = 0;
    for (size_t i = 0; i < set_tickers_.size(); ++i) {
      const auto& t = set_tickers_[i];
      if (t[0] == ticker_idx || t[1] == ticker_idx || t[2] == ticker_idx) {
        out[(*out_n)++] = static_cast<int>(i);
      }
    }
  }

  void set_indices(int set_i, int* range_i, int* lower_i, int* higher_i) const {
    const auto& t = set_tickers_[set_i];
    *range_i = t[0];
    *lower_i = t[1];
    *higher_i = t[2];
  }

 private:
  int ensure(const std::string& ticker) {
    auto it = ticker_to_idx_.find(ticker);
    if (it != ticker_to_idx_.end()) return it->second;
    if (n_ >= kMaxTickers) return 0;
    const int idx = n_++;
    ticker_to_idx_[ticker] = idx;
    tickers_[idx] = ticker;
    return idx;
  }

  AtomicBook books_[kMaxTickers];
  std::string tickers_[kMaxTickers];
  int n_{0};
  std::unordered_map<std::string, int> ticker_to_idx_;
  std::vector<MarketSet> sets_;
  std::vector<std::array<int, 3>> set_tickers_;
};
