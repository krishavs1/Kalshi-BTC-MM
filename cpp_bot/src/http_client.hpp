#pragma once

#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <tuple>
#include <vector>

#include <nlohmann/json.hpp>

#include "models.hpp"

struct HttpResponse {
  long status{0};
  std::string body;
  std::optional<nlohmann::json> json;
  int64_t elapsed_ns{0};
};

class HttpClient {
 public:
  HttpClient();
  ~HttpClient();

  std::optional<nlohmann::json> get_json(const std::string& url, const HeaderMap& headers,
                                         long timeout_ms = 1500);

  HttpResponse request(const std::string& method, const std::string& url, const HeaderMap& headers,
                       const std::string& body = "", long timeout_ms = 3000);

  std::vector<HttpResponse> request_multi(
      const std::vector<std::tuple<std::string, std::string, HeaderMap, std::string>>& reqs,
      long timeout_ms = 3000);

  void warm(const std::string& url, const HeaderMap& headers);

 private:
  void* share_{nullptr};
  std::mutex mu_;
};
