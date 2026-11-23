// Copyright 2026 Phinomenon Inc.
// Licensed under the Apache License, Version 2.0; see the LICENSE file.

// Tests of port/base: the base:: functions the core calls and upstream
// defines in the Windows-only client/compat/base.cc. Every expectation names
// the upstream lines it comes from ("Windows:", phibrowser-win at 2931e0e).

#include <cstdint>
#include <cstdio>
#include <limits>
#include <string>
#include <string_view>

#include "base/base64.h"
#include "base/strings/string_number_conversions.h"

namespace {

int g_checks = 0;
int g_failures = 0;

void ExpectEq(std::string_view actual,
              std::string_view expected,
              const char* what,
              int line) {
  ++g_checks;
  if (actual == expected) {
    return;
  }
  ++g_failures;
  std::fprintf(
      stderr, "base_test.cc:%d: %s\n  expected \"%.*s\"\n  actual   \"%.*s\"\n",
      line, what, static_cast<int>(expected.size()), expected.data(),
      static_cast<int>(actual.size()), actual.data());
}

#define EXPECT_EQ(actual, expected) \
  ExpectEq((actual), (expected), #actual, __LINE__)
#define EXPECT_TRUE(condition) \
  ExpectEq((condition) ? "true" : "false", "true", #condition, __LINE__)

// Windows: client/compat/base.cc:438-452 (EVP_EncodeBlock: the standard
// alphabet, '=' padding, no line breaks). Vectors: RFC 4648 section 10.
void TestBase64EncodeUsesTheStandardPaddedAlphabet() {
  EXPECT_EQ(base::Base64Encode(std::string_view("")), "");
  EXPECT_EQ(base::Base64Encode(std::string_view("f")), "Zg==");
  EXPECT_EQ(base::Base64Encode(std::string_view("fo")), "Zm8=");
  EXPECT_EQ(base::Base64Encode(std::string_view("foo")), "Zm9v");
  EXPECT_EQ(base::Base64Encode(std::string_view("foobar")), "Zm9vYmFy");
  // Bytes, not text: the caller passes a byte buffer (phi_tab_store.cc:17-19).
  EXPECT_EQ(base::Base64Encode(std::string_view("\x00\xff\xfe\xfb", 4)),
            "AP/++w==");
}

// Windows: client/compat/base.cc:454-470 (EVP_DecodeBase64).
void TestBase64DecodeReturnsTheBytes() {
  std::string out = "stale";
  EXPECT_TRUE(base::Base64Decode("Zm9vYmFy", &out));
  EXPECT_EQ(out, "foobar");
  EXPECT_TRUE(base::Base64Decode("Zg==", &out));
  EXPECT_EQ(out, "f");
  EXPECT_TRUE(base::Base64Decode("AP/++w==", &out));
  EXPECT_EQ(out, std::string_view("\x00\xff\xfe\xfb", 4));
  // No caller passes an empty string (phi_tab_store.cc:23). Upstream has no
  // branch for it (base.cc:454-470), so the result is EVP_DecodeBase64's:
  // success and no bytes.
  EXPECT_TRUE(base::Base64Decode("", &out));
  EXPECT_EQ(out, "");
}

// Windows: client/compat/base.cc:461-466: a failed decode returns false
// before *output is assigned. EVP_DecodeBase64 is strict: it wants the
// padding and takes no whitespace and no URL-safe alphabet.
void TestBase64DecodeRejectsBadInputAndLeavesTheOutputAlone() {
  for (std::string_view bad :
       {"Zg", "Zg=", "Zm9v\n", " Zm9v", "Zm9v!A==", "AP_--w==", "Z"}) {
    std::string out = "untouched";
    EXPECT_TRUE(!base::Base64Decode(bad, &out));
    EXPECT_EQ(out, "untouched");
  }
}

// Windows: client/compat/base.cc:113-118, 200, 202 (std::to_chars: decimal,
// '-' for negatives, nothing else). The callers are compat/json.cc:510 (int)
// and compat/json.cc:604 (int64_t, which is long here and long long on
// Windows, so the values are 64-bit on both).
void TestIntegersPrintAsPlainDecimal() {
  EXPECT_EQ(base::NumberToString(0), "0");
  EXPECT_EQ(base::NumberToString(16), "16");
  EXPECT_EQ(base::NumberToString(-7), "-7");
  EXPECT_EQ(base::NumberToString(std::numeric_limits<int>::max()),
            "2147483647");
  EXPECT_EQ(base::NumberToString(std::numeric_limits<int>::min()),
            "-2147483648");
  EXPECT_EQ(base::NumberToString(0L), "0");
  EXPECT_EQ(base::NumberToString(-42L), "-42");
  EXPECT_EQ(base::NumberToString(std::numeric_limits<int64_t>::max()),
            "9223372036854775807");
  EXPECT_EQ(base::NumberToString(std::numeric_limits<int64_t>::min()),
            "-9223372036854775808");
}

// The caller of the double overload is compat/json.cc:607.
// Windows: client/compat/base.cc:216-218.
void TestZeroKeepsItsSign() {
  EXPECT_EQ(base::NumberToString(0.0), "0");
  EXPECT_EQ(base::NumberToString(-0.0), "-0");
}

// Windows: client/compat/base.cc:213-248: the shortest digits that read back
// as the same double, written without an exponent while the decimal point
// sits in (-6, 12] (base.cc:241).
void TestDoublesInRangePrintAsPlainDecimals() {
  EXPECT_EQ(base::NumberToString(1.5), "1.5");
  EXPECT_EQ(base::NumberToString(-2.5), "-2.5");
  EXPECT_EQ(base::NumberToString(123.456), "123.456");
  EXPECT_EQ(base::NumberToString(0.1), "0.1");
  EXPECT_EQ(base::NumberToString(0.1 + 0.2), "0.30000000000000004");
  // base.cc:246-248: digits end before the point, zeros fill the gap.
  EXPECT_EQ(base::NumberToString(100.0), "100");
  EXPECT_EQ(base::NumberToString(-3.0), "-3");
  // base.cc:242-245: the point is before the digits.
  EXPECT_EQ(base::NumberToString(0.001), "0.001");
  EXPECT_EQ(base::NumberToString(0.0625), "0.0625");
  // The two ends of the range (base.cc:241).
  EXPECT_EQ(base::NumberToString(999999999999.0), "999999999999");
  EXPECT_EQ(base::NumberToString(123456789012.5), "123456789012.5");
  EXPECT_EQ(base::NumberToString(0.000001), "0.000001");
  EXPECT_EQ(base::NumberToString(0.0000015), "0.0000015");
}

// Windows: client/compat/base.cc:241, 256-264: outside that range one digit,
// the rest after a point if there is any, 'e', always a sign, and the
// exponent without leading zeros.
void TestDoublesOutOfRangePrintWithAnExponent() {
  EXPECT_EQ(base::NumberToString(1e12), "1e+12");
  EXPECT_EQ(base::NumberToString(1234567890123.0), "1.234567890123e+12");
  EXPECT_EQ(base::NumberToString(1.5e21), "1.5e+21");
  EXPECT_EQ(base::NumberToString(-1.5e21), "-1.5e+21");
  EXPECT_EQ(base::NumberToString(0.0000001), "1e-7");
  EXPECT_EQ(base::NumberToString(0.00000015), "1.5e-7");
  EXPECT_EQ(base::NumberToString(-0.00000015), "-1.5e-7");
  EXPECT_EQ(base::NumberToString(std::numeric_limits<double>::max()),
            "1.7976931348623157e+308");
  EXPECT_EQ(base::NumberToString(std::numeric_limits<double>::min()),
            "2.2250738585072014e-308");
  EXPECT_EQ(base::NumberToString(std::numeric_limits<double>::denorm_min()),
            "5e-324");
}

}  // namespace

int main() {
  TestBase64EncodeUsesTheStandardPaddedAlphabet();
  TestBase64DecodeReturnsTheBytes();
  TestBase64DecodeRejectsBadInputAndLeavesTheOutputAlone();
  TestIntegersPrintAsPlainDecimal();
  TestZeroKeepsItsSign();
  TestDoublesInRangePrintAsPlainDecimals();
  TestDoublesOutOfRangePrintWithAnExponent();
  if (g_failures) {
    std::fprintf(stderr, "base_test: %d failed\n", g_failures);
    return 1;
  }
  std::printf("base_test: %d checks passed\n", g_checks);
  return 0;
}
