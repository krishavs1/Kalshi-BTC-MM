#pragma once

#include <string>

#include "models.hpp"

HeaderMap get_auth_headers(const std::string& key_id, const std::string& private_key_path,
                           const std::string& method, const std::string& path);
