#pragma once

#include <optional>
#include <string>

#include <nlohmann/json.hpp>

#include "models.hpp"

class HttpClient {
 public:
  HttpClient();
  ~HttpClient();

  std::optional<nlohmann::json> get_json(const std::string& url, const HeaderMap& headers,
                                         long timeout_ms = 1500);
};
