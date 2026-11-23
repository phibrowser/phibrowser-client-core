// Copyright 2026 Phinomenon Inc.
// Licensed under the Apache License, Version 2.0; see the LICENSE file.

// The base64 functions the core calls (phi_tab_store.cc), declared in the
// imported core/client/compat/base/base64.h. Upstream defines them in the
// Windows-only client/compat/base.cc; these give the same results, from the
// same BoringSSL calls.

#include "base/base64.h"

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>

#include "third_party/boringssl/src/include/openssl/base64.h"

namespace base {

std::string Base64Encode(std::string_view input) {
  size_t length = 0;
  if (!EVP_EncodedLength(&length, input.size())) {
    return std::string();
  }
  // |length| counts the NUL that EVP_EncodeBlock writes after the text.
  std::string out(length, '\0');
  const size_t written = EVP_EncodeBlock(
      reinterpret_cast<uint8_t*>(out.data()),
      reinterpret_cast<const uint8_t*>(input.data()), input.size());
  out.resize(written);
  return out;
}

bool Base64Decode(std::string_view input, std::string* output) {
  size_t length = 0;
  if (!EVP_DecodedLength(&length, input.size())) {
    return false;
  }
  std::string out(length, '\0');
  size_t written = 0;
  if (!EVP_DecodeBase64(reinterpret_cast<uint8_t*>(out.data()), &written,
                        length, reinterpret_cast<const uint8_t*>(input.data()),
                        input.size())) {
    return false;
  }
  out.resize(written);
  *output = std::move(out);
  return true;
}

}  // namespace base
