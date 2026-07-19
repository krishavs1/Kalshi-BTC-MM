#include "order_client.hpp"

#include <cmath>
#include <iomanip>
#include <sstream>

#include "auth.hpp"
#include "config.hpp"

namespace {
std::string cents_to_dollars(int cents) {
  std::ostringstream oss;
  oss << std::fixed << std::setprecision(4) << (static_cast<double>(cents) / 100.0);
  return oss.str();
}

std::string count_fp(int count) {
  std::ostringstream oss;
  oss << std::fixed << std::setprecision(2) << static_cast<double>(count);
  return oss.str();
}

const char* book_side_str(BookSide side) { return side == BookSide::Bid ? "bid" : "ask"; }

double parse_fp(const nlohmann::json& j, const char* key, double fallback = 0.0) {
  if (!j.contains(key)) {
    return fallback;
  }
  if (j[key].is_number()) {
    return j[key].get<double>();
  }
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
  const double dollars = parse_fp(j, key, 0.0);
  return static_cast<int>(std::lround(dollars * 100.0));
}

OrderSubmitResult parse_create_response(const HttpResponse& resp, const std::string& client_order_id) {
  OrderSubmitResult out;
  out.status = resp.status;
  out.client_order_id = client_order_id;
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
  // Idempotent replay: conflict with existing client_order_id is treated as success path by caller
  // via get_order if needed.
  return out;
}
}  // namespace

OrderClient::OrderClient(HttpClient& http, std::string key_id, std::string private_key_path)
    : http_(http), key_id_(std::move(key_id)), private_key_path_(std::move(private_key_path)) {}

OrderSubmitResult OrderClient::create_order(const OrderSubmitRequest& req) {
  nlohmann::json body{
      {"ticker", req.ticker},
      {"side", book_side_str(req.side)},
      {"count", count_fp(req.count)},
      {"price", cents_to_dollars(req.price_cents)},
      {"time_in_force", "good_till_canceled"},
      {"self_trade_prevention_type", "taker_at_cross"},
      {"client_order_id", req.client_order_id},
      {"post_only", false},
  };

  auto headers = get_auth_headers(key_id_, private_key_path_, "POST", config::ORDERS_PATH);
  const std::string url = std::string(config::API_BASE) + "/portfolio/events/orders";
  auto resp = http_.request("POST", url, headers, body.dump(), config::HTTP_TIMEOUT_MS);
  return parse_create_response(resp, req.client_order_id);
}

std::vector<OrderSubmitResult> OrderClient::create_orders(const std::vector<OrderSubmitRequest>& reqs) {
  if (reqs.empty()) {
    return {};
  }
  if (reqs.size() == 1) {
    return {create_order(reqs.front())};
  }

  nlohmann::json orders = nlohmann::json::array();
  for (const auto& req : reqs) {
    orders.push_back({
        {"ticker", req.ticker},
        {"side", book_side_str(req.side)},
        {"count", count_fp(req.count)},
        {"price", cents_to_dollars(req.price_cents)},
        {"time_in_force", "good_till_canceled"},
        {"self_trade_prevention_type", "taker_at_cross"},
        {"client_order_id", req.client_order_id},
        {"post_only", false},
    });
  }
  nlohmann::json body{{"orders", orders}};

  const std::string path = std::string(config::ORDERS_PATH) + "/batched";
  auto headers = get_auth_headers(key_id_, private_key_path_, "POST", path);
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
      if (i < arr.size()) {
        const auto& item = arr[i];
        // Batch responses may wrap each entry as {order: {...}} or {error: ...}
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

  // Fallback: sequential submits if batch endpoint rejects.
  for (const auto& req : reqs) {
    results.push_back(create_order(req));
  }
  return results;
}

OrderCancelResult OrderClient::cancel_order(const std::string& order_id,
                                            const std::string& market_ticker) {
  OrderCancelResult out;
  const std::string path = std::string(config::ORDERS_PATH) + "/" + order_id;
  auto headers = get_auth_headers(key_id_, private_key_path_, "DELETE", path);
  std::string url = std::string(config::API_BASE) + "/portfolio/events/orders/" + order_id;
  if (!market_ticker.empty()) {
    url += "?market_ticker=" + market_ticker;
  }
  auto resp = http_.request("DELETE", url, headers, "", config::HTTP_TIMEOUT_MS);
  out.status = resp.status;
  if (resp.status == 200 || resp.status == 201) {
    out.ok = true;
    if (resp.json) {
      out.reduced_by = parse_fp(*resp.json, "reduced_by");
    }
    return out;
  }
  // Already gone / fully filled.
  if (resp.status == 404) {
    out.ok = true;
    out.error = "already_gone";
    return out;
  }
  out.error = resp.body;
  if (resp.json && resp.json->contains("error")) {
    out.error = (*resp.json)["error"].value("message", out.error);
  }
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
  auto headers = get_auth_headers(key_id_, private_key_path_, "POST", path);
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
  if (resp.json && resp.json->contains("error")) {
    out.error = (*resp.json)["error"].value("message", out.error);
  }
  return out;
}

OrderStatusResult OrderClient::get_order(const std::string& order_id) {
  OrderStatusResult out;
  const std::string path = std::string(config::GET_ORDER_PATH_PREFIX) + order_id;
  auto headers = get_auth_headers(key_id_, private_key_path_, "GET", path);
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
