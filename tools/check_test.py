#!/usr/bin/env python3
# Copyright 2026 Phinomenon Inc.
# Licensed under the Apache License, Version 2.0; see the LICENSE file.
"""Unit tests for check.py. Run: python3 tools/check_test.py

Each test runs the checks on a small temporary git repository shaped like
this one: an imported directory listed in SOURCE.json and files written
there. The tools are the real ones, found as check.py finds them for this
checkout; a missing tool fails these tests, it does not skip them.
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import check
import resync

HEADER = ("Copyright 2026 Phinomenon Inc.\n"
          "Licensed under the Apache License, Version 2.0; see the LICENSE "
          "file.\n")
CC_HEADER = "".join("// " + line for line in HEADER.splitlines(True))
PY_HEADER = "".join("# " + line for line in HEADER.splitlines(True))
PASSING_TEST = """import unittest


class T(unittest.TestCase):
    def test_it(self):
        self.assertEqual(1, 1)
if __name__ == "__main__":
    unittest.main()
"""


def real_tools(**changes):
    tools = check.find_tools(check.REPO)
    vars(tools).update(changes)
    return tools


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        subprocess.run(["git", "init", "-q", self.dir], check=True)
        # Imported: listed in the manifest, never looked at by a check.
        self.write("core/client/a.cc", "int   imported_a;\n")
        self.write("LICENSE", "licence text\n")
        self.write("port/base/x.cc", CC_HEADER + "\nint x = 1;\n")
        self.write("tools/t.py", PY_HEADER + "print(1)\n")
        self.write("tools/t_test.py", PY_HEADER + PASSING_TEST)
        self.write("README.md", "Plain ASCII.\n")
        self.write(".clang-format", "BasedOnStyle: Chromium\n")
        self.save_manifest()
        self.add()

    def save_manifest(self):
        files = [{"local": path, "upstream": path, "role": "r",
                  "mode": "100644", "sha256": resync.sha256(self.read(path))}
                 for path in ("core/client/a.cc", "LICENSE")]
        resync.save_manifest(self.dir, {
            "format": 1, "managed_dirs": ["core"],
            "groups": [{"name": "core", "description": "d", "origin": "o",
                        "commit": "0" * 40, "commit_date": "", "trees": [],
                        "pinned": False, "files": files}]})

    def write(self, path, text):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, path):
        with open(os.path.join(self.dir, path), "rb") as f:
            return f.read()

    def add(self):
        subprocess.run(["git", "-C", self.dir, "add", "-A"], check=True)

    def fast(self, tools=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            failed = check.check_fast(self.dir,
                                      tools or check.find_tools(check.REPO))
        return failed, out.getvalue()


class OwnFilesTest(RepoTest):
    def test_imported_files_are_not_ours(self):
        self.assertEqual(
            check.own_files(self.dir),
            [".clang-format", "README.md", "SOURCE.json", "port/base/x.cc",
             "tools/t.py", "tools/t_test.py"])

    def test_no_file_of_the_real_manifest_is_ours(self):
        own = set(check.own_files(check.REPO))
        self.assertIn("port/base/base64.cc", own)
        self.assertIn("tools/check.py", own)
        imported = [entry["local"]
                    for group in resync.load_manifest(check.REPO)["groups"]
                    for entry in group["files"]]
        self.assertGreater(len(imported), 500)
        self.assertEqual(own & set(imported), set())
        self.assertFalse([p for p in own if p.startswith(
            ("core/", "third_party/"))])


class AsciiTest(RepoTest):
    def test_a_clean_tree_passes(self):
        self.assertEqual(self.fast()[0], [])

    def test_a_non_ascii_character_fails_naming_file_line_and_column(self):
        self.write("README.md", "Plain.\nA dash \u2014 here.\n")
        failed, out = self.fast()
        self.assertEqual(failed, ["ascii"])
        self.assertIn("README.md:2:8: non-ASCII character U+2014 EM DASH",
                      out)

    def test_a_byte_that_is_not_utf8_fails(self):
        with open(os.path.join(self.dir, "tools/t.py"), "ab") as f:
            f.write(b"# \xff\n")
        failed, out = self.fast()
        self.assertEqual(failed, ["ascii"])
        self.assertIn("tools/t.py:4:3: non-ASCII character byte 0xFF", out)

    def test_an_imported_file_may_hold_anything(self):
        self.write("core/client/a.cc", "// caf\u00e9\n")
        self.save_manifest()
        self.assertEqual(self.fast()[0], [])

class CopyrightTest(RepoTest):
    def test_a_source_without_the_copyright_line_fails(self):
        self.write("port/base/x.cc", "int x = 1;\n")
        failed, out = self.fast()
        self.assertEqual(failed, ["copyright"])
        self.assertIn('port/base/x.cc:1: no "Copyright 2026 Phinomenon Inc."',
                      out)

    def test_a_script_without_the_licence_line_fails(self):
        self.write("tools/t.py", "# Copyright 2026 Phinomenon Inc.\n")
        failed, out = self.fast()
        self.assertEqual(failed, ["copyright"])
        self.assertIn('tools/t.py:1: no "Licensed under the Apache License, '
                      'Version 2.0; see the LICENSE file."', out)

    def test_a_header_below_the_first_five_lines_does_not_count(self):
        self.write("tools/t.py", "\n" * 5 + PY_HEADER)
        self.assertEqual(self.fast()[0], ["copyright"])

    def test_a_new_file_is_checked_once_it_is_added(self):
        self.write("port/base/new.h", "// no header\n")
        self.assertEqual(self.fast()[0], [])
        self.add()
        self.assertEqual(self.fast()[0], ["copyright"])


class SourceTest(RepoTest):
    def test_an_edited_imported_file_fails(self):
        self.write("core/client/a.cc", "int imported_a;\n")
        failed, out = self.fast()
        self.assertEqual(failed, ["source"])
        self.assertIn("core/client/a.cc: checksum differs", out)

    def test_an_unlisted_file_in_a_managed_directory_fails(self):
        self.write("core/client/ours.cc", CC_HEADER)
        failed, out = self.fast()
        self.assertEqual(failed, ["source"])
        self.assertIn("core/client/ours.cc: not in SOURCE.json", out)

class FormatTest(RepoTest):
    def test_an_unformatted_source_fails_naming_file_and_line(self):
        self.write("port/base/x.cc", CC_HEADER + "\nint   x=1;\n")
        failed, out = self.fast()
        self.assertEqual(failed, ["format"])
        self.assertIn("port/base/x.cc:4:4: error:", out)
        self.assertNotIn("core/client/a.cc", out)

    def test_without_clang_format_it_fails_and_the_rest_still_run(self):
        self.write("README.md", "caf\u00e9\n")
        failed, out = self.fast(real_tools(clang_format=None))
        self.assertEqual(failed, ["format", "ascii"])
        self.assertIn("git config phi.clang-format", out)
        self.assertNotIn("clang-format -i", out)

    def test_a_clang_format_that_cannot_run_fails(self):
        failed, out = self.fast(real_tools(clang_format="/nonexistent/cf"))
        self.assertEqual(failed, ["format"])
        self.assertIn("/nonexistent/cf", out)
        self.assertIn("git config phi.clang-format", out)
        self.assertNotIn("clang-format -i", out)


class NothingToCheckTest(RepoTest):
    def test_a_check_left_without_files_fails_instead_of_passing(self):
        subprocess.run(["git", "-C", self.dir, "rm", "-q", "--cached",
                        "port/base/x.cc"], check=True)
        failed, out = self.fast()
        self.assertEqual(failed, ["format"])
        self.assertIn("found nothing to check", out)


class LintTest(RepoTest):
    def test_an_unused_import_fails_naming_file_and_line(self):
        self.write("tools/t.py", PY_HEADER + "import os\nprint(1)\n")
        failed, out = self.fast()
        self.assertEqual(failed, ["pyflakes"])
        self.assertIn("tools/t.py:3:1: 'os' imported but unused", out)

    def test_a_lint_that_dies_without_a_word_fails(self):
        failed, out = self.fast(real_tools(pyflakes=["false"]))
        self.assertEqual(failed, ["pyflakes"])
        self.assertIn("exit status 1", out)

    def test_without_pyflakes_it_fails_saying_how_to_get_it(self):
        failed, out = self.fast(real_tools(
            pyflakes=[sys.executable, "-m", "no_such_module_here"]))
        self.assertEqual(failed, ["pyflakes"])
        self.assertIn("No module named no_such_module_here", out)
        self.assertIn("pip install pyflakes", out)
        self.assertNotIn("fix the findings", out)


class PythonTestsTest(RepoTest):
    def test_a_failing_script_test_fails_naming_it(self):
        self.write("tools/t_test.py", PY_HEADER + PASSING_TEST.replace(
            "(1, 1)", "(1, 2)"))
        failed, out = self.fast()
        self.assertEqual(failed, ["pytests"])
        self.assertIn("tools/t_test.py", out)
        self.assertIn("AssertionError: 1 != 2", out)

    def test_a_test_file_that_runs_no_test_fails(self):
        self.write("tools/t_test.py", PY_HEADER + PASSING_TEST.replace(
            "def test_it", "def it"))
        failed, out = self.fast()
        self.assertEqual(failed, ["pytests"])
        self.assertIn("tools/t_test.py: ran no test", out)

    def test_a_test_file_that_does_not_call_unittest_fails(self):
        self.write("tools/t_test.py", PY_HEADER + "print(1)\n")
        failed, out = self.fast()
        self.assertEqual(failed, ["pytests"])
        self.assertIn("tools/t_test.py: ran no test", out)

USE_AFTER_MOVE = """
#include <string>
#include <utility>

std::string Take(std::string s) {
  std::string t = std::move(s);
  return s + t;
}
"""


class TidyTest(RepoTest):
    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(check.REPO, ".clang-tidy"), self.dir)
        self.add()

    def tidy(self, tools=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            failed = check.run_checks("tidy", [check.TIDY], self.dir,
                                      tools or real_tools())
        return failed, out.getvalue()

    def test_a_clean_source_passes(self):
        failed, out = self.tidy()
        self.assertEqual(failed, [], out)
        self.assertIn("1 file(s)", out)

    def test_a_use_after_move_fails_naming_file_line_and_check(self):
        self.write("port/base/x.cc", CC_HEADER + USE_AFTER_MOVE)
        failed, out = self.tidy()
        self.assertEqual(failed, ["tidy"])
        self.assertIn("port/base/x.cc:9:10:", out)
        self.assertIn("[bugprone-use-after-move", out)

    def test_a_header_of_port_is_checked_through_the_source_including_it(
            self):
        self.write("port/base/b.h", CC_HEADER + "#ifndef PORT_BASE_B_H_\n"
                   "#define PORT_BASE_B_H_\n"
                   "inline int bad_name() { return 1; }\n"
                   "#endif  // PORT_BASE_B_H_\n")
        self.write("port/base/x.cc", CC_HEADER + '#include "port/base/b.h"\n'
                   "int Use() { return bad_name(); }\n")
        self.add()
        failed, out = self.tidy()
        self.assertEqual(failed, ["tidy"])
        self.assertIn("port/base/b.h:5:12:", out)
        self.assertIn("[readability-identifier-naming", out)

    def test_an_imported_header_is_not_analysed(self):
        self.write("core/client/a.h", "inline int bad_name() { return 1; }\n")
        self.write("port/base/x.cc", CC_HEADER + '#include "client/a.h"\n'
                   "int Use() { return bad_name(); }\n")
        self.assertEqual(self.tidy()[0], [])

    def test_a_source_that_does_not_compile_fails(self):
        self.write("port/base/x.cc", CC_HEADER + '#include "nowhere.h"\n')
        failed, out = self.tidy()
        self.assertEqual(failed, ["tidy"])
        self.assertIn("'nowhere.h' file not found", out)

    def test_without_clang_tidy_it_fails_saying_how_to_get_it(self):
        for missing in (None, "/nonexistent/ct"):
            failed, out = self.tidy(real_tools(clang_tidy=missing))
            self.assertEqual(failed, ["tidy"])
            self.assertIn("git config phi.clang-tidy", out)
            self.assertIn("clang-tidy-18", out)
            self.assertNotIn("fix the findings", out)

    def test_a_clang_tidy_that_dies_without_a_word_fails(self):
        failed, out = self.tidy(real_tools(clang_tidy=shutil.which("false")))
        self.assertEqual(failed, ["tidy"])
        self.assertIn("exit status 1", out)

    def test_the_real_tree_gives_it_every_source_of_port(self):
        sources = check.tidy_sources(check.REPO)
        for path in ("port/base/base64.cc",
                     "port/base/string_number_conversions.cc",
                     "port/tests/base_test.cc"):
            self.assertIn(path, sources)
        self.assertFalse([p for p in sources if not p.startswith("port/")])

# Stands in for build/build.py: logs its arguments, prints the lines the
# real one prints for two test binaries, fails when asked to.
FAKE_BUILD = PY_HEADER + """import os
import sys

with open(os.path.join(os.path.dirname(__file__), "log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_BUILD") == "silent":
    sys.exit(0)
print("  a_test   3/3 passed  0.01s avg  | a_test: all passed")
print("  b_test   3/3 passed  0.01s avg  | b_test: all passed")
if os.environ.get("FAKE_BUILD") == " ".join(sys.argv[1:5]):
    print("FAILED at step: link")
    sys.exit(1)
"""


class FullCheckTest(RepoTest):
    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(check.REPO, ".clang-tidy"), self.dir)
        self.write("build/build.py", FAKE_BUILD)
        self.add()
        self.addCleanup(os.environ.pop, "FAKE_BUILD", None)

    def full(self, build_args=("--framework-include", "/fw")):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            failed = check.check_full(self.dir, real_tools(),
                                      list(build_args))
        return failed, out.getvalue()

    def log(self):
        return self.read("build/log").decode().splitlines()

    def test_a_clean_tree_passes_and_builds_all_four_configurations(self):
        failed, out = self.full()
        self.assertEqual(failed, [], out)
        self.assertEqual(self.log(), [
            "--cxx %s --std %s --repeat 3 --framework-include /fw" % pair
            for pair in (("clang++", "c++20"), ("clang++", "c++23"),
                         ("g++", "c++20"), ("g++", "c++23"))])
        self.assertIn("tidy ", out)
        self.assertIn("2 test binaries", out)

    def test_a_failed_configuration_is_named_and_the_rest_still_run(self):
        os.environ["FAKE_BUILD"] = "--cxx g++ --std c++20"
        failed, out = self.full()
        self.assertEqual(failed, ["build g++ c++20"])
        self.assertIn("FAILED at step: link", out)
        self.assertEqual(len(self.log()), 4)

    def test_a_build_that_reports_no_test_binary_fails(self):
        os.environ["FAKE_BUILD"] = "silent"
        failed, out = self.full()
        self.assertEqual(len(failed), 4)
        self.assertIn("found nothing to check", out)

    def test_fast_and_tidy_findings_are_reported_and_the_builds_still_run(
            self):
        self.write("README.md", "caf\u00e9\n")
        self.write("port/base/x.cc", CC_HEADER + USE_AFTER_MOVE)
        self.assertEqual(self.full()[0], ["ascii", "tidy"])
        self.assertEqual(len(self.log()), 4)

    def test_without_framework_is_passed_through(self):
        self.full(["--without-framework"])
        self.assertEqual(len(self.log()), 4)
        self.assertTrue(all(line.endswith("--repeat 3 --without-framework")
                            for line in self.log()))


class OptionsTest(unittest.TestCase):
    def main(self, *argv, environ=None):
        # No check may start: a fast check of the real tree would run this
        # file again, without end.
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out), \
                mock.patch.object(check, "run_checks",
                                  side_effect=AssertionError("ran")):
            try:
                code = check.main(list(argv), environ=environ or {})
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue()

    def test_a_mode_is_required(self):
        self.assertEqual(self.main()[0], 2)

    def test_full_without_a_framework_option_stops_and_names_both(self):
        code, out = self.main("--full")
        self.assertEqual(code, 1)
        self.assertIn("--framework-include", out)
        self.assertIn("--without-framework", out)
        self.assertNotIn(" ok ", out)

    def test_fast_takes_no_framework_option(self):
        for option in (["--without-framework"],
                       ["--framework-include", "/fw"],
                       ["--framework-include=/fw"]):
            self.assertEqual(self.main("--fast", *option)[0], 2, option)


if __name__ == "__main__":
    unittest.main()
