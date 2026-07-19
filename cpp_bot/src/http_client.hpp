#pragma once

#include <optional>
#include <string>

#include <nlohmann/json.hpp>

#include "models.hpp"

struct HttpResponse {
  long status{0};
  std::string body;
  std::optional<nlohmann::json> json;
};

class HttpClient {
 public:
  HttpClient();
  ~HttpClient();

  std::optional<nlohmann::json> get_json(const std::string& url, const HeaderMap& headers,
                                         long timeout_ms = 1500);

  HttpResponse request(const std::string& method, const std::string& url, const HeaderMap& headers,
                       const std::string& body = "", long timeout_ms = 3000);
};
