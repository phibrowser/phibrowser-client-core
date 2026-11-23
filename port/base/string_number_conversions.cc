// Copyright 2026 Phinomenon Inc.
// Licensed under the Apache License, Version 2.0; see the LICENSE file.

// The base::NumberToString overloads the core calls (compat/json.cc),
// declared in the imported
// core/client/compat/base/strings/string_number_conversions.h. Upstream
// defines them in the Windows-only client/compat/base.cc; these give the
// same text, because what one platform writes another reads.

#include "base/strings/string_number_conversions.h"

#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <string>
#include <string_view>

namespace base {

namespace {

template <typename T>
std::string IntegerToString(T value) {
  std::array<char, 32> buffer;
  const auto result =
      std::to_chars(buffer.data(), buffer.data() + buffer.size(), value);
  return std::string(buffer.data(), result.ptr);
}

}  // namespace

std::string NumberToString(int value) {
  return IntegerToString(value);
}
std::string NumberToString(long value) {
  return IntegerToString(value);
}

// The shortest digits that read back as |value|, laid out as upstream lays
// them out (and as Chromium's base does): plain decimals while the decimal
// point sits in (-6, 12], otherwise "1.5e+21". Same steps as upstream's
// client/compat/base.cc, so that the text is the same on every platform.
std::string NumberToString(double value) {
  if (value == 0) {
    return std::signbit(value) ? "-0" : "0";
  }
  std::array<char, 64> buffer;
  const auto result =
      std::to_chars(buffer.data(), buffer.data() + buffer.size(), value,
                    std::chars_format::scientific);
  std::string_view text(buffer.data(),
                        static_cast<size_t>(result.ptr - buffer.data()));
  const bool negative = text[0] == '-';
  if (negative) {
    text.remove_prefix(1);
  }
  // |text| is "d.ddde+XX" or "de+XX".
  const size_t e = text.find('e');
  std::string digits(text.substr(0, 1));
  if (e > 2) {
    digits += text.substr(2, e - 2);
  }
  // |exponent| stays 0 if no number follows; to_chars always writes one for
  // a finite value.
  int exponent = 0;
  std::string_view exponent_text = text.substr(e + 1);
  if (exponent_text[0] == '+') {
    exponent_text.remove_prefix(1);
  }
  std::from_chars(exponent_text.data(),
                  exponent_text.data() + exponent_text.size(), exponent);
  const int decimal_point = exponent + 1;
  std::string out = negative ? "-" : "";
  if (decimal_point > -6 && decimal_point <= 12) {
    if (decimal_point <= 0) {
      out += "0.";
      out.append(static_cast<size_t>(-decimal_point), '0');
      out += digits;
    } else if (static_cast<size_t>(decimal_point) >= digits.size()) {
      out += digits;
      out.append(static_cast<size_t>(decimal_point) - digits.size(), '0');
    } else {
      out += digits.substr(0, static_cast<size_t>(decimal_point));
      out += '.';
      out += digits.substr(static_cast<size_t>(decimal_point));
    }
    return out;
  }
  out += digits[0];
  if (digits.size() > 1) {
    out += '.';
    out += digits.substr(1);
  }
  out += 'e';
  out += exponent < 0 ? '-' : '+';
  out += std::to_string(std::abs(exponent));
  return out;
}

}  // namespace base
