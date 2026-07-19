#include "auth.hpp"

#include <chrono>
#include <fstream>
#include <sstream>
#include <stdexcept>

#include <openssl/bio.h>
#include <openssl/buffer.h>
#include <openssl/evp.h>
#include <openssl/pem.h>

namespace {
std::string read_file(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    throw std::runtime_error("Failed to open private key: " + path);
  }
  std::stringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

std::string base64_encode(const unsigned char* data, size_t len) {
  BIO* bio = BIO_new(BIO_s_mem());
  BIO* b64 = BIO_new(BIO_f_base64());
  BIO_set_flags(b64, BIO_FLAGS_BASE64_NO_NL);
  bio = BIO_push(b64, bio);
  BIO_write(bio, data, static_cast<int>(len));
  BIO_flush(bio);
  BUF_MEM* buffer_ptr{};
  BIO_get_mem_ptr(bio, &buffer_ptr);
  std::string encoded(buffer_ptr->data, buffer_ptr->length);
  BIO_free_all(bio);
  return encoded;
}

EVP_PKEY* load_pkey(const std::string& private_key_path) {
  std::string key_pem = read_file(private_key_path);
  BIO* key_bio = BIO_new_mem_buf(key_pem.data(), static_cast<int>(key_pem.size()));
  EVP_PKEY* pkey = PEM_read_bio_PrivateKey(key_bio, nullptr, nullptr, nullptr);
  BIO_free(key_bio);
  if (!pkey) {
    throw std::runtime_error("Failed to parse private key");
  }
  return pkey;
}

HeaderMap sign_with_pkey(EVP_PKEY* pkey, const std::string& key_id, const std::string& method,
                         const std::string& path) {
  const auto now = std::chrono::time_point_cast<std::chrono::milliseconds>(
      std::chrono::system_clock::now());
  const auto ts_ms = std::to_string(now.time_since_epoch().count());
  const std::string message = ts_ms + method + path;

  EVP_MD_CTX* ctx = EVP_MD_CTX_new();
  if (!ctx) {
    throw std::runtime_error("Failed to create digest context");
  }

  if (EVP_DigestSignInit(ctx, nullptr, EVP_sha256(), nullptr, pkey) != 1) {
    EVP_MD_CTX_free(ctx);
    throw std::runtime_error("EVP_DigestSignInit failed");
  }

  EVP_PKEY_CTX* pctx = EVP_MD_CTX_pkey_ctx(ctx);
  if (!pctx || EVP_PKEY_CTX_set_rsa_padding(pctx, RSA_PKCS1_PSS_PADDING) <= 0 ||
      EVP_PKEY_CTX_set_rsa_pss_saltlen(pctx, -1) <= 0) {
    EVP_MD_CTX_free(ctx);
    throw std::runtime_error("Failed to configure RSA-PSS");
  }

  if (EVP_DigestSignUpdate(ctx, message.data(), message.size()) != 1) {
    EVP_MD_CTX_free(ctx);
    throw std::runtime_error("EVP_DigestSignUpdate failed");
  }

  size_t sig_len = 0;
  if (EVP_DigestSignFinal(ctx, nullptr, &sig_len) != 1) {
    EVP_MD_CTX_free(ctx);
    throw std::runtime_error("Failed to fetch signature length");
  }

  std::string signature(sig_len, '\0');
  if (EVP_DigestSignFinal(ctx, reinterpret_cast<unsigned char*>(&signature[0]), &sig_len) != 1) {
    EVP_MD_CTX_free(ctx);
    throw std::runtime_error("EVP_DigestSignFinal failed");
  }
  signature.resize(sig_len);
  EVP_MD_CTX_free(ctx);

  return HeaderMap{
      {"KALSHI-ACCESS-KEY", key_id},
      {"KALSHI-ACCESS-SIGNATURE",
       base64_encode(reinterpret_cast<const unsigned char*>(signature.data()), signature.size())},
      {"KALSHI-ACCESS-TIMESTAMP", ts_ms},
      {"Content-Type", "application/json"},
  };
}
}  // namespace

AuthSigner::AuthSigner(std::string key_id, std::string private_key_path)
    : key_id_(std::move(key_id)), private_key_path_(std::move(private_key_path)) {
  pkey_ = load_pkey(private_key_path_);
}

AuthSigner::~AuthSigner() {
  if (pkey_) {
    EVP_PKEY_free(static_cast<EVP_PKEY*>(pkey_));
    pkey_ = nullptr;
  }
}

HeaderMap AuthSigner::sign(const std::string& method, const std::string& path) {
  std::lock_guard<std::mutex> lock(mu_);
  return sign_with_pkey(static_cast<EVP_PKEY*>(pkey_), key_id_, method, path);
}

HeaderMap get_auth_headers(const std::string& key_id, const std::string& private_key_path,
                           const std::string& method, const std::string& path) {
  EVP_PKEY* pkey = load_pkey(private_key_path);
  try {
    auto headers = sign_with_pkey(pkey, key_id, method, path);
    EVP_PKEY_free(pkey);
    return headers;
  } catch (...) {
    EVP_PKEY_free(pkey);
    throw;
  }
}
