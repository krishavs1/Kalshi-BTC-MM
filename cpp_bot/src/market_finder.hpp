#pragma once

#include <optional>
#include <string>
#include <vector>

#include "http_client.hpp"
#include "models.hpp"

struct MarketDiscoveryResult {
  std::vector<MarketSet> market_sets;
  std::string csv_filename;
  std::string date_str;
};

std::optional<MarketDiscoveryResult> find_and_setup_markets(HttpClient& http, const std::string& key_id,
                                                            const std::string& private_key_path,
                                                            bool init_csv);
