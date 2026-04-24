#pragma once

#include <string>
#include <vector>

#include "models.hpp"

ProfitPair calculate_profits(const Orderbook* range_ob, const Orderbook* lower_ob,
                             const Orderbook* higher_ob);

void init_profit_csv(const std::string& csv_filename, int num_ranges);
void log_profits_to_csv(const std::string& csv_filename, const std::vector<ProfitPair>& profits);
