#!/usr/bin/env python3
# Copyright 2026 Phinomenon Inc.
# Licensed under the Apache License, Version 2.0; see the LICENSE file.
"""Unit tests for build.py. Run: python3 build/build_test.py"""

import contextlib
import io
import os
import subprocess
import tempfile
import types
import unittest

import build

HEADER = os.path.join(*build.FRAMEWORK_HEADER.split("/"))


class FrameworkIncludeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)
        return full

    def test_a_source_including_the_framework_header_needs_it(self):
        client = os.path.join(self.dir, "core", "client")
        self.write("core/client/a.cc", '#include "%s"\n' % build.FRAMEWORK_HEADER)
        self.write("core/client/b.h", "#include <string>\nclass PhiFramework;\n")
        self.write("core/client/b.cc", '#include "client/b.h"\n')
        self.write("core/client/c.h", '#include "client/a.h"\n')
        self.write("core/client/a.h",
                   '#include "chrome/browser/phinomenon/phi_framework/phi_ids.h"\n')
        self.assertTrue(build.needs_framework("a.cc", client))
        self.assertFalse(build.needs_framework("b.cc", client))
        self.assertTrue(build.needs_framework("c.h", client))

    def test_the_real_core_needs_it_in_one_translation_unit(self):
        sources, headers = build.core_sources()
        self.assertEqual(
            [p for p in sources + headers if build.needs_framework(p)],
            ["phi_chromium_sync.cc"])

    def test_resolve_exposes_only_the_framework_directory(self):
        self.write("fw/" + HEADER, "// api\n")
        self.write("fw/base/values.h", "// must stay out of reach\n")
        out = os.path.join(self.dir, "out")
        root = build.resolve_framework(os.path.join(self.dir, "fw"), out)
        self.assertTrue(os.path.isfile(os.path.join(root, HEADER)))
        self.assertFalse(os.path.exists(os.path.join(root, "base")))
        self.assertTrue(root.startswith(out))

    def test_two_roots_resolve_to_two_directories(self):
        self.write("one/" + HEADER, "// 9\n")
        self.write("two/" + HEADER, "// 10\n")
        out = os.path.join(self.dir, "out")
        self.assertNotEqual(
            build.resolve_framework(os.path.join(self.dir, "one"), out),
            build.resolve_framework(os.path.join(self.dir, "two"), out))

    def test_a_root_without_the_header_is_refused(self):
        with self.assertRaises(build.Failure) as raised:
            build.resolve_framework(self.dir, os.path.join(self.dir, "out"))
        self.assertIn(build.FRAMEWORK_HEADER, str(raised.exception))

    def test_no_option_fails_early_and_names_both_options(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = build.main(["--out", os.path.join(self.dir, "out")],
                              environ={})
        self.assertEqual(code, 1)
        self.assertIn("--framework-include", out.getvalue())
        self.assertIn("--without-framework", out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.dir, "out")))


class PortTest(unittest.TestCase):
    def test_port_sources_leave_out_the_tests(self):
        sources = build.port_sources()
        self.assertIn(os.path.join("base", "base64.cc"), sources)
        self.assertFalse([p for p in sources if p.startswith("tests")])

    def test_every_port_source_is_run_by_a_port_test(self):
        # A test may also link core sources, spelled "../core/..." here.
        tested = {source for _, sources, _, _, _ in build.PORT_TESTS
                  for source in sources if not source.startswith("..")}
        self.assertEqual(sorted(tested), build.port_sources())

    def test_nothing_is_expected_to_be_undefined(self):
        self.assertEqual(build.read_expected(), [])

ARGS = types.SimpleNamespace(cxx="clang++", std="c++20", jobs=4, repeat=2)
NO_VENDOR = {name: {"plain": [], "san": []} for name in ("sqlite", "crypto")}


class StepTest(unittest.TestCase):
    """Runs a step with the real compiler on sources in a temporary
    directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.out = os.path.join(self.dir, "out")

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def run_step(self, step, *args, **kwargs):
        """(the step's result or the Failure it raised, what it printed)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                result = step(ARGS, self.out, *args, **kwargs)
            except build.Failure as failure:
                result = failure
        return result, out.getvalue()


class LinkTest(StepTest):
    def link(self, source, expected):
        path = self.write("a.cc", source)
        subprocess.run([ARGS.cxx, "-c", path, "-o", path + ".o"], check=True)
        return self.run_step(build.step_link, [path + ".o"], NO_VENDOR,
                             expected)

    def test_objects_that_need_nothing_else_pass(self):
        result, out = self.link("int Defined() { return 1; }\n", [])
        self.assertIsNone(result)
        self.assertIn("0 undefined symbol(s), 0 expected", out)

    def test_a_new_undefined_symbol_fails_and_is_named(self):
        result, out = self.link(
            "namespace ns { int Missing(int); }\n"
            "int Use() { return ns::Missing(1); }\n", [])
        self.assertEqual(str(result), "link")
        self.assertIn("  new undefined symbol: ns::Missing(int)\n", out)

    def test_an_expected_undefined_symbol_passes(self):
        result, _ = self.link(
            "namespace ns { int Missing(int); }\n"
            "int Use() { return ns::Missing(1); }\n", ["ns::Missing(int)"])
        self.assertIsNone(result)

    def test_an_expected_symbol_that_is_now_defined_fails_and_is_named(self):
        result, out = self.link("int Defined() { return 1; }\n",
                                ["ns::Missing(int)"])
        self.assertEqual(str(result), "link")
        self.assertIn("  no longer undefined: ns::Missing(int)\n", out)

    def test_a_link_that_fails_for_another_reason_fails_with_its_output(
            self):
        result, out = self.link("int main() { return 1; }\n", [])
        self.assertEqual(str(result), "link")
        self.assertIn("main", out)

    def test_the_wording_of_each_linker_is_understood(self):
        # What clang++ 18 printed for one missing ns::Missing(int) with GNU
        # ld 2.42, gold 1.16 and the framework's lld (2026-11-21).
        for text in (
                "/usr/bin/ld: m.o: in function `main':\n"
                "m.cc:(.text+0x15): undefined reference to "
                "`ns::Missing(int)'\n",
                "m.o:m.cc:function main:(.text+0x15): error: undefined "
                "reference to 'ns::Missing(int)'\n",
                "ld.lld: error: undefined symbol: ns::Missing(int)\n"
                ">>> referenced by m.cc\n>>>               m.o:(main)\n"):
            self.assertEqual(build.undefined_symbols(text),
                             ["ns::Missing(int)"], text)


PASSING = """#include <cstdio>
int main() {
  std::printf("t_test: all passed\\n");
  return 0;
}
"""


class TestsTest(StepTest):
    def tables(self, source, passed=r"t_test: all passed"):
        self.write("tests/t_test.cc", source)
        self.write("src/lib.cc", "int Lib() { return 1; }\n")
        return [([("t_test", ["lib.cc"], [], [], passed)],
                 os.path.join(self.dir, "src"),
                 os.path.join(self.dir, "tests"))]

    def build_and_run(self, source, **kwargs):
        binaries, out = self.run_step(
            build.step_tests, NO_VENDOR, self.tables(source, **kwargs))
        if isinstance(binaries, build.Failure):
            return binaries, out
        return self.run_step(build.step_run, binaries)

    def test_a_passing_test_is_built_and_run_the_asked_number_of_times(self):
        result, out = self.build_and_run(PASSING)
        self.assertIsNone(result, out)
        self.assertIn("2/2 passed", out)

    def test_a_test_is_built_without_exceptions(self):
        source = PASSING.replace(
            "int main() {", "int main() {\n  try {\n    throw 1;\n"
            "  } catch (int) {\n  }")
        result, out = self.build_and_run(source)
        self.assertEqual(str(result), "tests")
        self.assertIn("exceptions disabled", out)

    def test_a_test_with_a_warning_does_not_build(self):
        result, out = self.build_and_run(
            PASSING.replace("int main() {", "int main() {\n  int unused;"))
        self.assertEqual(str(result), "tests")
        self.assertIn("-Wunused-variable", out)

    def test_a_test_that_does_not_link_fails(self):
        result, out = self.build_and_run(
            "int Nowhere();\nint main() { return Nowhere(); }\n")
        self.assertEqual(str(result), "tests")
        self.assertIn("FAILED link t_test", out)

    def test_a_failing_exit_status_fails_and_shows_the_output(self):
        result, out = self.build_and_run(
            PASSING.replace("return 0;", "return 3;"))
        self.assertEqual(str(result), "run")
        self.assertIn("FAILED t_test (exit 3)", out)
        self.assertIn("0/2 passed", out)

    def test_a_leak_fails_the_run(self):
        result, out = self.build_and_run(PASSING.replace(
            "int main() {", "int* g_leak;\nint main() {\n"
            "  g_leak = new int[4];\n  g_leak = nullptr;"))
        self.assertEqual(str(result), "run")
        self.assertIn("LeakSanitizer", out)

    def test_undefined_behaviour_fails_the_run(self):
        result, out = self.build_and_run(PASSING.replace(
            "int main() {", "int main(int argc, char**) {\n"
            "  int big = 2147483647;\n  big += argc;\n"
            "  if (big == 0) {\n    return 1;\n  }"))
        self.assertEqual(str(result), "run")
        self.assertIn("signed integer overflow", out)

    def test_a_test_that_exits_0_having_printed_nothing_fails(self):
        result, out = self.build_and_run("int main() { return 0; }\n")
        self.assertEqual(str(result), "run")
        self.assertIn("FAILED t_test: exit 0, but its last line", out)
        self.assertIn("0/2 passed", out)

    def test_a_test_that_reports_zero_cases_fails(self):
        result, out = self.build_and_run(
            PASSING.replace("all passed", "0 cases agree"),
            passed=r"t_test: [1-9]\d* cases agree")
        self.assertEqual(str(result), "run")
        self.assertIn('"t_test: 0 cases agree"', out)

    def test_every_real_test_says_what_its_last_line_must_be(self):
        for table, _, _ in build.test_tables():
            for test, _, _, _, passed in table:
                self.assertTrue(passed.startswith(test + ": "), test)


if __name__ == "__main__":
    unittest.main()
