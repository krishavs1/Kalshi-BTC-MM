#pragma once

#include <optional>
#include <string>
#include <vector>

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
};

struct OrderStatusResult {
  bool ok{false};
  long status{0};
  std::string order_id;
  std::string order_status;  // resting / canceled / executed
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
  OrderClient(HttpClient& http, std::string key_id, std::string private_key_path);

  OrderSubmitResult create_order(const OrderSubmitRequest& req);
  std::vector<OrderSubmitResult> create_orders(const std::vector<OrderSubmitRequest>& reqs);
  OrderCancelResult cancel_order(const std::string& order_id, const std::string& market_ticker);
  OrderAmendResult amend_order(const std::string& order_id, const OrderSubmitRequest& req);
  OrderStatusResult get_order(const std::string& order_id);

 private:
  HttpClient& http_;
  std::string key_id_;
  std::string private_key_path_;
};
