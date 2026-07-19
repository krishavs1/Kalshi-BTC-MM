#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "auth.hpp"
#include "http_client.hpp"
#include "models.hpp"

struct OrderSubmitRequest {
  std::string ticker;
  BookSide side{BookSide::Bid};
  int price_cents{0};
  int count{1};
  std::string client_order_id;
};

struct OrderSubmitResult {
  bool ok{false};
  long status{0};
  std::string order_id;
  std::string client_order_id;
  double fill_count{0.0};
  double remaining_count{0.0};
  std::string error;
  int64_t http_elapsed_ns{0};
};

struct OrderStatusResult {
  bool ok{false};
  long status{0};
  std::string order_id;
  std::string order_status;
  double fill_count{0.0};
  double remaining_count{0.0};
  int price_cents{0};
  std::string error;
};

struct OrderCancelResult {
  bool ok{false};
  long status{0};
  double reduced_by{0.0};
  std::string error;
};

struct OrderAmendResult {
  bool ok{false};
  long status{0};
  std::string order_id;
  double fill_count{0.0};
  double remaining_count{0.0};
  std::string error;
};

class OrderClient {
 public:
  OrderClient(HttpClient& http, AuthSigner& signer);

  OrderSubmitResult create_order(const OrderSubmitRequest& req);
  // Prefer batch: one TLS RTT + one RSA signature for all legs.
  std::vector<OrderSubmitResult> create_orders(const std::vector<OrderSubmitRequest>& reqs);
  OrderCancelResult cancel_order(const std::string& order_id, const std::string& market_ticker);
  OrderAmendResult amend_order(const std::string& order_id, const OrderSubmitRequest& req);
  OrderStatusResult get_order(const std::string& order_id);

  void warm_connections();

 private:
  HttpClient& http_;
  AuthSigner& signer_;
};
