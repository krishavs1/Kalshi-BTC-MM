#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <vector>

inline int64_t mono_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

inline int64_t mono_us() { return mono_ns() / 1000; }

class LatencyStats {
 public:
  explicit LatencyStats(const char* name, size_t cap = 4096) : name_(name), samples_(cap, 0) {}

  void record_ns(int64_t ns) {
    if (ns < 0) return;
    std::lock_guard<std::mutex> lock(mu_);
    samples_[idx_ % samples_.size()] = ns;
    ++idx_;
    if (count_ < samples_.size()) ++count_;
  }

  void record_us(int64_t us) { record_ns(us * 1000); }

  void maybe_report(int64_t every_n = 64) {
    std::lock_guard<std::mutex> lock(mu_);
    ++since_report_;
    if (since_report_ < every_n || count_ == 0) return;
    since_report_ = 0;

    std::vector<int64_t> tmp(samples_.begin(), samples_.begin() + static_cast<std::ptrdiff_t>(count_));
    std::sort(tmp.begin(), tmp.end());
    const auto pct = [&](double p) -> int64_t {
      if (tmp.empty()) return 0;
      size_t i = static_cast<size_t>(p * (tmp.size() - 1));
      return tmp[i];
    };
    std::cout << "[latency] " << name_ << " n=" << count_ << " p50=" << (pct(0.50) / 1000.0)
              << "us p90=" << (pct(0.90) / 1000.0) << "us p99=" << (pct(0.99) / 1000.0)
              << "us max=" << (tmp.back() / 1000.0) << "us\n";
  }

 private:
  const char* name_;
  std::vector<int64_t> samples_;
  size_t idx_{0};
  size_t count_{0};
  int64_t since_report_{0};
  std::mutex mu_;
};

struct HotPathClocks {
  int64_t ws_recv_ns{0};
  int64_t decide_ns{0};
  int64_t submit_start_ns{0};
  int64_t submit_done_ns{0};
};
