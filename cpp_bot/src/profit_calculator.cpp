#include "profit_calculator.hpp"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>

ProfitPair calculate_profits(const Orderbook* range_ob, const Orderbook* lower_ob,
                             const Orderbook* higher_ob) {
  if (!range_ob || !lower_ob || !higher_ob) {
    return ProfitPair{};
  }

  const double profit1 =
      (range_ob->yes_ask - 1.0) - lower_ob->yes_ask - higher_ob->no_ask + 100.0;
  const double profit2 = (range_ob->no_ask - 1.0) - lower_ob->no_ask - higher_ob->yes_ask + 100.0;
  return ProfitPair{profit1, profit2, true};
}

void init_profit_csv(const std::string& csv_filename, int num_ranges) {
  std::filesystem::create_directories(std::filesystem::path(csv_filename).parent_path());
  const bool exists = std::filesystem::exists(csv_filename);
  std::ofstream out(csv_filename, std::ios::app);
  if (!exists) {
    out << "Time";
    for (int i = 1; i <= num_ranges; ++i) {
      out << ",Range" << i << " Profit 1 (Range YES limit)"
          << ",Range" << i << " Profit 2 (Range NO limit)";
    }
    out << "\n";
  }
}

void log_profits_to_csv(const std::string& csv_filename, const std::vector<ProfitPair>& profits) {
  std::ofstream out(csv_filename, std::ios::app);
  if (!out) {
    return;
  }

  auto now = std::chrono::system_clock::now();
  std::time_t t = std::chrono::system_clock::to_time_t(now);
  std::tm tm = *std::localtime(&t);
  out << std::put_time(&tm, "%Y-%m-%d %H:%M:%S");

  for (const auto& p : profits) {
    if (p.valid) {
      out << "," << std::fixed << std::setprecision(2) << p.profit1 << "," << p.profit2;
    } else {
      out << ",0.00,0.00";
    }
  }
  out << "\n";
}
