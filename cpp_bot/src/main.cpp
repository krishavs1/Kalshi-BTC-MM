#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include "smart_monitor.hpp"

int main() {
  const char* key_id_env = std::getenv("KALSHI_KEY_ID");
  const char* key_path_env = std::getenv("KALSHI_PRIVATE_KEY_PATH");
  const std::string key_id = key_id_env ? key_id_env : "";
  const std::string key_path = key_path_env ? key_path_env : "kalshi-key.pem";

  if (key_id.empty()) {
    std::cerr << "Set KALSHI_KEY_ID before running.\n";
    return 1;
  }

  try {
    std::cout << "Starting C++ smart monitor...\n";
    run_monitor(key_id, key_path);
  } catch (const std::exception& e) {
    std::cerr << "Fatal error: " << e.what() << "\n";
    return 1;
  }

  return 0;
}
