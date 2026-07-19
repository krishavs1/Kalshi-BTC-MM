#include "http_client.hpp"

#include <chrono>
#include <tuple>

#include <curl/curl.h>

#include "latency.hpp"

namespace {
size_t write_callback(void* contents, size_t size, size_t nmemb, void* userp) {
  const size_t total = size * nmemb;
  auto* out = static_cast<std::string*>(userp);
  out->append(static_cast<char*>(contents), total);
  return total;
}

void apply_fast_opts(CURL* curl, long timeout_ms) {
  curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, timeout_ms);
  curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, timeout_ms);
  curl_easy_setopt(curl, CURLOPT_TCP_NODELAY, 1L);
  curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(curl, CURLOPT_HTTP_VERSION, CURL_HTTP_VERSION_2TLS);
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
}

struct curl_slist* build_headers(const HeaderMap& headers) {
  struct curl_slist* header_list = nullptr;
  for (const auto& [k, v] : headers) {
    header_list = curl_slist_append(header_list, (k + ": " + v).c_str());
  }
  // Prefer keep-alive.
  header_list = curl_slist_append(header_list, "Connection: keep-alive");
  return header_list;
}
}  // namespace

HttpClient::HttpClient() {
  curl_global_init(CURL_GLOBAL_DEFAULT);
  share_ = curl_share_init();
  auto* share = static_cast<CURLSH*>(share_);
  curl_share_setopt(share, CURLSHOPT_SHARE, CURL_LOCK_DATA_DNS);
  curl_share_setopt(share, CURLSHOPT_SHARE, CURL_LOCK_DATA_SSL_SESSION);
  curl_share_setopt(share, CURLSHOPT_SHARE, CURL_LOCK_DATA_CONNECT);
}

HttpClient::~HttpClient() {
  if (share_) {
    curl_share_cleanup(static_cast<CURLSH*>(share_));
    share_ = nullptr;
  }
  curl_global_cleanup();
}

HttpResponse HttpClient::request(const std::string& method, const std::string& url,
                                 const HeaderMap& headers, const std::string& body,
                                 long timeout_ms) {
  std::lock_guard<std::mutex> lock(mu_);
  HttpResponse resp;
  CURL* curl = curl_easy_init();
  if (!curl) {
    return resp;
  }

  struct curl_slist* header_list = build_headers(headers);
  const int64_t t0 = mono_ns();

  curl_easy_setopt(curl, CURLOPT_SHARE, static_cast<CURLSH*>(share_));
  curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
  curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
  apply_fast_opts(curl, timeout_ms);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &resp.body);
  curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, method.c_str());

  if (!body.empty() && (method == "POST" || method == "PUT" || method == "PATCH")) {
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, static_cast<long>(body.size()));
  }

  const auto rc = curl_easy_perform(curl);
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &resp.status);
  resp.elapsed_ns = mono_ns() - t0;

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
    }
  }
  return resp;
}

std::vector<HttpResponse> HttpClient::request_multi(
    const std::vector<std::tuple<std::string, std::string, HeaderMap, std::string>>& reqs,
    long timeout_ms) {
  std::lock_guard<std::mutex> lock(mu_);
  std::vector<HttpResponse> out(reqs.size());
  if (reqs.empty()) return out;

  CURLM* multi = curl_multi_init();
  struct Easy {
    CURL* curl{nullptr};
    curl_slist* headers{nullptr};
    std::string body_storage;
  };
  std::vector<Easy> easies(reqs.size());

  const int64_t t0 = mono_ns();
  for (size_t i = 0; i < reqs.size(); ++i) {
    const auto& [method, url, headers, body] = reqs[i];
    easies[i].curl = curl_easy_init();
    easies[i].body_storage = body;
    easies[i].headers = build_headers(headers);
    CURL* c = easies[i].curl;
    curl_easy_setopt(c, CURLOPT_SHARE, static_cast<CURLSH*>(share_));
    curl_easy_setopt(c, CURLOPT_URL, url.c_str());
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, easies[i].headers);
    apply_fast_opts(c, timeout_ms);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &out[i].body);
    curl_easy_setopt(c, CURLOPT_CUSTOMREQUEST, method.c_str());
    curl_easy_setopt(c, CURLOPT_PRIVATE, reinterpret_cast<char*>(i));
    if (!easies[i].body_storage.empty() &&
        (method == "POST" || method == "PUT" || method == "PATCH")) {
      curl_easy_setopt(c, CURLOPT_POSTFIELDS, easies[i].body_storage.c_str());
      curl_easy_setopt(c, CURLOPT_POSTFIELDSIZE, static_cast<long>(easies[i].body_storage.size()));
    }
    curl_multi_add_handle(multi, c);
  }

  int still = 0;
  do {
    CURLMcode mc = curl_multi_perform(multi, &still);
    if (mc != CURLM_OK) break;
    if (still) {
      curl_multi_poll(multi, nullptr, 0, 50, nullptr);
    }
  } while (still);

  const int64_t elapsed = mono_ns() - t0;
  CURLMsg* msg = nullptr;
  int msgs = 0;
  while ((msg = curl_multi_info_read(multi, &msgs))) {
    if (msg->msg != CURLMSG_DONE) continue;
    char* priv = nullptr;
    curl_easy_getinfo(msg->easy_handle, CURLINFO_PRIVATE, &priv);
    const size_t i = reinterpret_cast<size_t>(priv);
    curl_easy_getinfo(msg->easy_handle, CURLINFO_RESPONSE_CODE, &out[i].status);
    out[i].elapsed_ns = elapsed;
    if (!out[i].body.empty()) {
      try {
        out[i].json = nlohmann::json::parse(out[i].body);
      } catch (...) {
      }
    }
  }

  for (auto& e : easies) {
    curl_multi_remove_handle(multi, e.curl);
    curl_slist_free_all(e.headers);
    curl_easy_cleanup(e.curl);
  }
  curl_multi_cleanup(multi);
  return out;
}

void HttpClient::warm(const std::string& url, const HeaderMap& headers) {
  // HEAD/GET to establish TLS session + connection cache.
  request("GET", url, headers, "", 2000);
}

std::optional<nlohmann::json> HttpClient::get_json(const std::string& url, const HeaderMap& headers,
                                                   long timeout_ms) {
  auto resp = request("GET", url, headers, "", timeout_ms);
  if (resp.status != 200) {
    return std::nullopt;
  }
  return resp.json;
}
