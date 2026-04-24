#include "http_client.hpp"

#include <curl/curl.h>

namespace {
size_t write_callback(void* contents, size_t size, size_t nmemb, void* userp) {
  const size_t total = size * nmemb;
  auto* out = static_cast<std::string*>(userp);
  out->append(static_cast<char*>(contents), total);
  return total;
}
}  // namespace

HttpClient::HttpClient() { curl_global_init(CURL_GLOBAL_DEFAULT); }

HttpClient::~HttpClient() { curl_global_cleanup(); }

std::optional<nlohmann::json> HttpClient::get_json(const std::string& url, const HeaderMap& headers,
                                                   long timeout_ms) {
  CURL* curl = curl_easy_init();
  if (!curl) {
    return std::nullopt;
  }

  std::string body;
  struct curl_slist* header_list = nullptr;
  for (const auto& [k, v] : headers) {
    header_list = curl_slist_append(header_list, (k + ": " + v).c_str());
  }

  curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
  curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, timeout_ms);
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &body);

  const auto rc = curl_easy_perform(curl);
  long status = 0;
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);

  curl_slist_free_all(header_list);
  curl_easy_cleanup(curl);

  if (rc != CURLE_OK || status != 200) {
    return std::nullopt;
  }

  try {
    return nlohmann::json::parse(body);
  } catch (...) {
    return std::nullopt;
  }
}
