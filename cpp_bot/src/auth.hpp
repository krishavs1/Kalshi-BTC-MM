#pragma once

#include <mutex>
#include <string>

#include "models.hpp"

// Cached RSA signer — loads the PEM once instead of on every request.
class AuthSigner {
 public:
  AuthSigner(std::string key_id, std::string private_key_path);
  ~AuthSigner();

  AuthSigner(const AuthSigner&) = delete;
  AuthSigner& operator=(const AuthSigner&) = delete;

  HeaderMap sign(const std::string& method, const std::string& path);

  const std::string& key_id() const { return key_id_; }

 private:
  std::string key_id_;
  std::string private_key_path_;
  void* pkey_{nullptr};  // EVP_PKEY*
  std::mutex mu_;
};

// Backward-compatible helper (slower — reloads key). Prefer AuthSigner.
HeaderMap get_auth_headers(const std::string& key_id, const std::string& private_key_path,
                           const std::string& method, const std::string& path);
