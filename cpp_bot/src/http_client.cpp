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

HttpResponse HttpClient::request(const std::string& method, const std::string& url,
                                 const HeaderMap& headers, const std::string& body,
                                 long timeout_ms) {
  HttpResponse resp;
  CURL* curl = curl_easy_init();
  if (!curl) {
    return resp;
  }

  struct curl_slist* header_list = nullptr;
  for (const auto& [k, v] : headers) {
    header_list = curl_slist_append(header_list, (k + ": " + v).c_str());
  }

  curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
  curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, timeout_ms);
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &resp.body);
  curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, method.c_str());

  if (!body.empty() && (method == "POST" || method == "PUT" || method == "PATCH")) {
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, static_cast<long>(body.size()));
  }

  const auto rc = curl_easy_perform(curl);
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &resp.status);

  curl_slist_free_all(header_list);
  curl_easy_cleanup(curl);

  if (rc != CURLE_OK) {
    resp.status = 0;
    return resp;
  }

  if (!resp.body.empty()) {
    try {
      resp.json = nlohmann::json::parse(resp.body);
    } catch (...) {
      // leave json unset for non-JSON bodies
    }
  }
  return resp;
}

std::optional<nlohmann::json> HttpClient::get_json(const std::string& url, const HeaderMap& headers,
                                                   long timeout_ms) {
  auto resp = request("GET", url, headers, "", timeout_ms);
  if (resp.status != 200) {
    return std::nullopt;
  }
  return resp.json;
}
