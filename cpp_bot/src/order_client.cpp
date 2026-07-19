#include "order_client.hpp"

#include <cmath>
#include <cstdio>
#include <iomanip>
#include <sstream>

#include "config.hpp"

namespace {
std::string cents_to_dollars(int cents) {
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%.4f", static_cast<double>(cents) / 100.0);
  return buf;
}

std::string count_fp(int count) {
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%.2f", static_cast<double>(count));
  return buf;
}

const char* book_side_str(BookSide side) { return side == BookSide::Bid ? "bid" : "ask"; }

double parse_fp(const nlohmann::json& j, const char* key, double fallback = 0.0) {
  if (!j.contains(key)) return fallback;
  if (j[key].is_number()) return j[key].get<double>();
  if (j[key].is_string()) {
    try {
      return std::stod(j[key].get<std::string>());
    } catch (...) {
      return fallback;
    }
  }
  return fallback;
}

int dollars_to_cents(const nlohmann::json& j, const char* key) {
  return static_cast<int>(std::lround(parse_fp(j, key, 0.0) * 100.0));
}

OrderSubmitResult parse_create_response(const HttpResponse& resp, const std::string& client_order_id) {
  OrderSubmitResult out;
  out.status = resp.status;
  out.client_order_id = client_order_id;
  out.http_elapsed_ns = resp.elapsed_ns;
  if ((resp.status == 200 || resp.status == 201) && resp.json) {
    const auto& root = *resp.json;
    const auto& o = root.contains("order") ? root["order"] : root;
    out.ok = true;
    out.order_id = o.value("order_id", "");
    out.fill_count = parse_fp(o, "fill_count", parse_fp(o, "fill_count_fp"));
    out.remaining_count = parse_fp(o, "remaining_count", parse_fp(o, "remaining_count_fp"));
    return out;
  }
  out.error = resp.body;
  if (resp.json && resp.json->contains("error")) {
    out.error = (*resp.json)["error"].value("message", out.error);
  }
  return out;
}

nlohmann::json order_body(const OrderSubmitRequest& req) {
  return nlohmann::json{
      {"ticker", req.ticker},
      {"side", book_side_str(req.side)},
      {"count", count_fp(req.count)},
      {"price", cents_to_dollars(req.price_cents)},
      {"time_in_force", "good_till_canceled"},
      {"self_trade_prevention_type", "taker_at_cross"},
      {"client_order_id", req.client_order_id},
      {"post_only", false},
  };
}
}  // namespace

OrderClient::OrderClient(HttpClient& http, AuthSigner& signer) : http_(http), signer_(signer) {}

void OrderClient::warm_connections() {
  auto headers = signer_.sign("GET", "/trade-api/v2/exchange/status");
  http_.warm(std::string(config::API_BASE) + "/exchange/status", headers);
  // Also warm the orders host path with an OPTIONS-less GET of balance if available.
  auto bal = signer_.sign("GET", "/trade-api/v2/portfolio/balance");
  http_.warm(std::string(config::API_BASE) + "/portfolio/balance", bal);
}

OrderSubmitResult OrderClient::create_order(const OrderSubmitRequest& req) {
  const auto body = order_body(req).dump();
  auto headers = signer_.sign("POST", config::ORDERS_PATH);
  const std::string url = std::string(config::API_BASE) + "/portfolio/events/orders";
  auto resp = http_.request("POST", url, headers, body, config::HTTP_TIMEOUT_MS);
  return parse_create_response(resp, req.client_order_id);
}

std::vector<OrderSubmitResult> OrderClient::create_orders(const std::vector<OrderSubmitRequest>& reqs) {
  if (reqs.empty()) return {};
  if (reqs.size() == 1) return {create_order(reqs.front())};

  nlohmann::json orders = nlohmann::json::array();
  for (const auto& req : reqs) {
    orders.push_back(order_body(req));
  }
  nlohmann::json body{{"orders", orders}};

  const std::string path = std::string(config::ORDERS_PATH) + "/batched";
  auto headers = signer_.sign("POST", path);
  const std::string url = std::string(config::API_BASE) + "/portfolio/events/orders/batched";
  auto resp = http_.request("POST", url, headers, body.dump(), config::HTTP_TIMEOUT_MS);

  std::vector<OrderSubmitResult> results;
  results.reserve(reqs.size());

  if ((resp.status == 200 || resp.status == 201) && resp.json) {
    const auto& root = *resp.json;
    const auto& arr = root.contains("orders") ? root["orders"] : nlohmann::json::array();
    for (size_t i = 0; i < reqs.size(); ++i) {
      OrderSubmitResult r;
      r.client_order_id = reqs[i].client_order_id;
      r.status = resp.status;
      r.http_elapsed_ns = resp.elapsed_ns;
      if (i < arr.size()) {
        const auto& item = arr[i];
        if (item.contains("error")) {
          r.ok = false;
          r.error = item["error"].is_string() ? item["error"].get<std::string>()
                                              : item["error"].value("message", item.dump());
        } else {
          const auto& o = item.contains("order") ? item["order"] : item;
          r.ok = true;
          r.order_id = o.value("order_id", "");
          r.fill_count = parse_fp(o, "fill_count", parse_fp(o, "fill_count_fp"));
          r.remaining_count = parse_fp(o, "remaining_count", parse_fp(o, "remaining_count_fp"));
        }
      } else {
        r.ok = false;
        r.error = "missing batch entry";
      }
      results.push_back(r);
    }
    return results;
  }

  // Fallback: parallel individual creates (still one RSA each, but concurrent RTT).
  std::vector<std::tuple<std::string, std::string, HeaderMap, std::string>> multi;
  multi.reserve(reqs.size());
  std::vector<std::string> bodies;
  bodies.reserve(reqs.size());
  for (const auto& req : reqs) {
    bodies.push_back(order_body(req).dump());
    auto h = signer_.sign("POST", config::ORDERS_PATH);
    multi.emplace_back("POST", std::string(config::API_BASE) + "/portfolio/events/orders", std::move(h),
                       bodies.back());
  }
  // Careful: bodies.back() references invalidated if vector grows — already reserved, OK.
  // But we moved strings into multi by copying bodies.back() at emplace — actually we pass
  // bodies.back() which copies into tuple. Good.

  // Fix: the tuple stores a copy of the string from bodies.back() at construction time.
  auto resps = http_.request_multi(multi, config::HTTP_TIMEOUT_MS);
  results.clear();
  for (size_t i = 0; i < reqs.size(); ++i) {
    results.push_back(parse_create_response(resps[i], reqs[i].client_order_id));
  }
  return results;
}

OrderCancelResult OrderClient::cancel_order(const std::string& order_id,
                                            const std::string& market_ticker) {
  OrderCancelResult out;
  const std::string path = std::string(config::ORDERS_PATH) + "/" + order_id;
  auto headers = signer_.sign("DELETE", path);
  std::string url = std::string(config::API_BASE) + "/portfolio/events/orders/" + order_id;
  if (!market_ticker.empty()) {
    url += "?market_ticker=" + market_ticker;
  }
  auto resp = http_.request("DELETE", url, headers, "", config::HTTP_TIMEOUT_MS);
  out.status = resp.status;
  if (resp.status == 200 || resp.status == 201 || resp.status == 404) {
    out.ok = true;
    if (resp.json) out.reduced_by = parse_fp(*resp.json, "reduced_by");
    if (resp.status == 404) out.error = "already_gone";
    return out;
  }
  out.error = resp.body;
  return out;
}

OrderAmendResult OrderClient::amend_order(const std::string& order_id, const OrderSubmitRequest& req) {
  OrderAmendResult out;
  const std::string path = std::string(config::ORDERS_PATH) + "/" + order_id + "/amend";
  nlohmann::json body{
      {"ticker", req.ticker},
      {"side", book_side_str(req.side)},
      {"price", cents_to_dollars(req.price_cents)},
      {"count", count_fp(req.count)},
      {"client_order_id", req.client_order_id},
  };
  auto headers = signer_.sign("POST", path);
  const std::string url =
      std::string(config::API_BASE) + "/portfolio/events/orders/" + order_id + "/amend";
  auto resp = http_.request("POST", url, headers, body.dump(), config::HTTP_TIMEOUT_MS);
  out.status = resp.status;
  if ((resp.status == 200 || resp.status == 201) && resp.json) {
    out.ok = true;
    const auto& root = *resp.json;
    const auto& o = root.contains("order") ? root["order"] : root;
    out.order_id = o.value("order_id", order_id);
    out.fill_count = parse_fp(o, "fill_count", parse_fp(o, "fill_count_fp"));
    out.remaining_count = parse_fp(o, "remaining_count", parse_fp(o, "remaining_count_fp"));
    return out;
  }
  out.error = resp.body;
  return out;
}

OrderStatusResult OrderClient::get_order(const std::string& order_id) {
  OrderStatusResult out;
  const std::string path = std::string(config::GET_ORDER_PATH_PREFIX) + order_id;
  auto headers = signer_.sign("GET", path);
  const std::string url = std::string(config::API_BASE) + "/portfolio/orders/" + order_id;
  auto resp = http_.request("GET", url, headers, "", config::HTTP_TIMEOUT_MS);
  out.status = resp.status;
  if (resp.status == 200 && resp.json) {
    const auto& o = (*resp.json).contains("order") ? (*resp.json)["order"] : *resp.json;
    out.ok = true;
    out.order_id = o.value("order_id", order_id);
    out.order_status = o.value("status", "");
    out.fill_count = parse_fp(o, "fill_count_fp", parse_fp(o, "fill_count"));
    out.remaining_count = parse_fp(o, "remaining_count_fp", parse_fp(o, "remaining_count"));
    if (o.contains("yes_price")) {
      out.price_cents = o.value("yes_price", 0);
    } else {
      out.price_cents = dollars_to_cents(o, "yes_price_dollars");
    }
    return out;
  }
  out.error = resp.body;
  return out;
}
