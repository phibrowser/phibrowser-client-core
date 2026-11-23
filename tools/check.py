#!/usr/bin/env python3
# Copyright 2026 Phinomenon Inc.
# Licensed under the Apache License, Version 2.0; see the LICENSE file.
"""Checks the files written in this repository.

  tools/check.py --fast    what the pre-commit hook runs
                           (tools/install-hooks.py); no build of the core
  tools/check.py --full --framework-include DIR
                           the check before review: --fast, --tidy, and
                           build/build.py (every step, each test binary
                           three times) with clang++ and g++, as C++20 and
                           as C++23; DIR and --without-framework are
                           build.py's (default: $PHI_FRAMEWORK_INCLUDE)
  tools/check.py --tidy    clang-tidy alone

The files that came from the Windows client (everything SOURCE.json lists,
and its managed directories) are never formatted, linted or read by a style
check: the core keeps the style it arrived in, and the vendored libraries are
not ours. Every other file git tracks is "written here".

It reads the working tree, so stage everything you have changed: a new file
is seen once it is added.

Fast checks:

  source     every file SOURCE.json lists exists, the vendored ones still
             match it byte for byte and its directories hold no unlisted
             file (tools/resync.py verify).
  copyright  every .cc, .h, .py and .sh written here starts with the
             copyright line and the licence line.
  format     clang-format (check only, .clang-format: Chromium style) on
             every .cc and .h written here.
  ascii      every file written here is ASCII.
  pyflakes   pyflakes on every .py written here.
  pytests    every *_test.py written here passes and runs at least one
             test.

Full adds:

  tidy       clang-tidy (.clang-tidy, every finding an error) on every .cc
             written here and the headers under port/ they include.
  build ...  build/build.py in each of the four configurations.

Every check runs, whatever the others did, and prints one line with what it
looked at; a failed one prints its findings and how to fix them. A check
that finds nothing to look at fails: the files exist, so that would be a
broken check.

Tools. clang-format: the path in this checkout's git configuration
(git config phi.clang-format PATH), else clang-format on PATH; use the
framework's (src/buildtools/linux64-format/clang-format), which is the one
the client and the framework are checked with. clang-tidy: phi.clang-tidy,
else clang-tidy-18 on PATH. pyflakes: a module of the Python that runs this
script. A tool that is missing fails its check; nothing is skipped.
"""

import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import time
import types
import unicodedata

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "tools"), os.path.join(REPO, "build")]
import build  # noqa: E402  (build/, on the path just above)
import resync  # noqa: E402  (tools/, likewise)


def own_files(repo):
    """The files git tracks in |repo| that were written here: not listed in
    SOURCE.json and not under one of its managed directories. Sorted."""
    manifest = resync.load_manifest(repo)
    imported = {entry["local"] for group in manifest["groups"]
                for entry in group["files"]}
    managed = tuple(name + "/" for name in manifest["managed_dirs"])
    tracked = subprocess.run(["git", "-C", repo, "ls-files", "-z"],
                             check=True, capture_output=True,
                             text=True).stdout.split("\0")
    return sorted(path for path in tracked
                  if path and path not in imported
                  and not path.startswith(managed)
                  and os.path.exists(os.path.join(repo, path)))


def check_ascii(repo, tools):
    """(files looked at, findings)."""
    del tools
    findings = []
    paths = own_files(repo)
    for path in paths:
        with open(os.path.join(repo, path), "rb") as f:
            text = f.read().decode("utf-8", errors="surrogateescape")
        for number, line in enumerate(text.split("\n"), 1):
            for column, char in enumerate(line, 1):
                code = ord(char)
                if code <= 0x7F:
                    continue
                if 0xDC80 <= code <= 0xDCFF:  # A byte that is not UTF-8.
                    what = "byte 0x%02X (not UTF-8)" % (code - 0xDC00)
                else:
                    what = ("U+%04X %s" % (
                        code, unicodedata.name(char, ""))).strip()
                findings.append("%s:%d:%d: non-ASCII character %s" % (
                    path, number, column, what))
    return len(paths), "\n".join(findings)


COPYRIGHT = ("Copyright 2026 Phinomenon Inc.",
             "Licensed under the Apache License, Version 2.0; see the "
             "LICENSE file.")
COPYRIGHT_LINES = 5
COPYRIGHT_TYPES = (".cc", ".h", ".py", ".sh")


def check_copyright(repo, tools):
    del tools
    findings = []
    paths = [p for p in own_files(repo) if p.endswith(COPYRIGHT_TYPES)]
    for path in paths:
        with open(os.path.join(repo, path), encoding="utf-8",
                  errors="replace") as f:
            head = f.read().split("\n")[:COPYRIGHT_LINES]
        findings += ['%s:1: no "%s" in the first %d lines' % (
            path, wanted, COPYRIGHT_LINES)
            for wanted in COPYRIGHT
            if not any(wanted in line for line in head)]
    return len(paths), "\n".join(findings)


def check_source(repo, tools):
    """The tree still matches SOURCE.json (tools/resync.py verify)."""
    del tools
    manifest = resync.load_manifest(repo)
    return (sum(len(group["files"]) for group in manifest["groups"]),
            "\n".join(resync.verify(repo, manifest)))


class Unusable(Exception):
    """A tool a check needs is missing or cannot run; the text says what to
    do about it."""


TIMEOUT = 1800  # Seconds; the slowest step is a build from nothing.


def run_tool(command, cwd):
    """(exit status, output) of |command|, which gets no standard input. A
    command that cannot start or does not end in TIMEOUT gives status 1 and
    the error; one that fails without a word gives its exit status as its
    output, so a failure is never empty."""
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True,
                                text=True, errors="replace",
                                stdin=subprocess.DEVNULL, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, "%s\n" % error
    output = result.stdout + result.stderr
    if result.returncode and not output.strip():
        output = "%s: exit status %d and no output\n" % (
            command[0], result.returncode)
    return result.returncode, output


def check_format(repo, tools):
    """clang-format, check only, with the repository's .clang-format."""
    hint = ("name a clang-format that runs with\n"
            "  git config phi.clang-format PATH\n"
            "(a framework checkout has "
            "src/buildtools/linux64-format/clang-format), or put one on PATH")
    paths = [p for p in own_files(repo) if p.endswith((".cc", ".h"))]
    if not paths:
        return 0, ""
    if not tools.clang_format:
        raise Unusable("no clang-format: " + hint)
    status, output = run_tool(
        [tools.clang_format, "--dry-run", "-Werror", "--style=file", *paths],
        repo)
    if status and "[-Wclang-format-violations]" not in output:
        raise Unusable("%s%s did not run (exit status %d): %s" % (
            output, tools.clang_format, status, hint))
    return len(paths), output if status else ""


def check_pyflakes(repo, tools):
    paths = [p for p in own_files(repo) if p.endswith(".py")]
    if not paths:
        return 0, ""
    status, output = run_tool([*tools.pyflakes, *paths], repo)
    if status and "No module named" in output:
        raise Unusable(
            "%sthe Python that runs this script has no pyflakes: "
            "%s -m pip install pyflakes" % (output, sys.executable))
    return len(paths), output if status else ""


def check_pytests(repo, tools):
    """Every *_test.py written here, each as its own process."""
    del tools
    paths = [p for p in own_files(repo) if p.endswith("_test.py")]
    with concurrent.futures.ThreadPoolExecutor(len(paths) or 1) as pool:
        results = list(pool.map(
            lambda path: run_tool([sys.executable, path], repo), paths))
    findings = []
    for path, (status, output) in zip(paths, results):
        ran = re.search(r"^Ran (\d+) tests? in ", output, re.M)
        if status:
            findings.append("%s: failed\n%s" % (path, output.rstrip("\n")))
        elif not ran or ran.group(1) == "0":
            findings.append("%s: ran no test" % path)
    return len(paths), "\n".join(findings)


def tidy_sources(repo):
    return [p for p in own_files(repo) if p.endswith(".cc")]


def check_tidy(repo, tools):
    """clang-tidy with the repository's .clang-tidy on every .cc written
    here, and on the headers under port/ they include. The compile flags
    are the compile step's (build.py), rooted at |repo|."""
    paths = tidy_sources(repo)
    if not tools.clang_tidy or not os.path.exists(tools.clang_tidy):
        raise Unusable(
            "no clang-tidy (%s): apt install clang-tidy-18, or name one "
            "with\n  git config phi.clang-tidy PATH" % tools.clang_tidy)
    flags = [flag.replace(build.REPO, repo) for flag in
             ["-std=c++20", *build.CORE_FLAGS, *build.WARNINGS,
              *build.INCLUDES]]
    command = [tools.clang_tidy, "--quiet",
               "--config-file=" + os.path.join(repo, ".clang-tidy"),
               "--header-filter=^" + re.escape(os.path.join(repo, "port"))
               + "/"]
    with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4) as pool:
        results = list(pool.map(
            lambda path: run_tool([*command, path, "--", *flags], repo),
            paths))
    return len(paths), "".join(output for status, output in results
                               if status)


CONFIGURATIONS = [("clang++", "c++20"), ("clang++", "c++23"),
                  ("g++", "c++20"), ("g++", "c++23")]
REPEAT = "3"


def check_build(cxx, std, build_args):
    """A check that runs build/build.py (every step, each test binary
    REPEAT times) for one compiler and standard. It counts the test
    binaries the build reports as passed."""
    def run(repo, tools):
        del tools
        status, output = run_tool(
            [sys.executable, os.path.join(repo, "build", "build.py"),
             "--cxx", cxx, "--std", std, "--repeat", REPEAT, *build_args],
            repo)
        passed = re.findall(r"^  \S+ +%s/%s passed " % (REPEAT, REPEAT),
                            output, re.M)
        return len(passed), output if status else ""
    return run


# (name, check, what it counts, how to fix a finding). A check returns (how
# many of those it looked at, its findings as text: empty when clean).
FILES = "file(s)"
FAST = [
    ("source", check_source, "listed " + FILES,
     "a vendored file is never edited by hand: restore it (git checkout); "
     "a new or removed file under a managed directory needs its entry in "
     "SOURCE.json added or deleted (SOURCE.md)"),
    ("copyright", check_copyright, FILES,
     "start the file with the two lines the other files written here start "
     "with"),
    ("format", check_format, FILES, "run clang-format -i on the files above"),
    ("ascii", check_ascii, FILES,
     "replace each character with ASCII (-- for a dash, ' for a curly "
     "quote, \\uXXXX in a string)"),
    ("pyflakes", check_pyflakes, FILES, "fix the findings above"),
    ("pytests", check_pytests, "test " + FILES,
     "fix the failing tests above"),
]
TIDY = ("tidy", check_tidy, FILES,
        "fix the findings above (checks: .clang-tidy; to turn one off, do it "
        "there and say why)")


def run_checks(tier, checks, repo, tools):
    """Runs |checks| in order, every one whatever the others did; returns
    the names of the failed ones."""
    failed = []
    for name, function, unit, fix in checks:
        start = time.monotonic()
        try:
            count, findings = function(repo, tools)
        except Unusable as unusable:
            count, findings, fix = 1, str(unusable), "see above"
        if not findings and not count:
            findings = "found nothing to check"
            fix = "the check is broken, or what it is for is gone"
        if findings:
            failed.append(name)
            print(findings.rstrip("\n"))
            print("check: %s: %s FAILED; %s" % (tier, name, fix))
        else:
            print("%-18s ok (%d %s, %.1fs)" % (
                name, count, unit, time.monotonic() - start))
    return failed


def find_tools(repo):
    """Where the tools are for the checkout |repo|. A tool named in the
    checkout's git configuration (phi.<name>) wins over one on PATH; a
    tool that is not found is None, and its check fails saying so."""
    def configured(name, on_path):
        result = subprocess.run(
            ["git", "-C", repo, "config", "--get", "phi." + name],
            capture_output=True, text=True)
        return result.stdout.strip() or shutil.which(on_path)

    return types.SimpleNamespace(
        clang_format=configured("clang-format", "clang-format"),
        clang_tidy=configured("clang-tidy", "clang-tidy-18"),
        pyflakes=[sys.executable, "-m", "pyflakes"])


def check_fast(repo, tools):
    return run_checks("fast", FAST, repo, tools)


def check_full(repo, tools, build_args):
    """The fast checks, clang-tidy, then the build in each of
    CONFIGURATIONS with |build_args| (build.py's framework option)."""
    builds = [("build %s %s" % (cxx, std), check_build(cxx, std, build_args),
               "test binaries, each %s times" % REPEAT,
               "fix what the build reports above (docs/BUILD.md)")
              for cxx, std in CONFIGURATIONS]
    return run_checks("full", FAST + [TIDY] + builds, repo, tools)


def main(argv=None, environ=os.environ):
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0], epilog="See docs/CHECKS.md.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fast", action="store_true",
                      help="the pre-commit checks; no build of the core")
    mode.add_argument("--tidy", action="store_true",
                      help="clang-tidy alone")
    mode.add_argument("--full", action="store_true",
                      help="the checks before review: --fast, --tidy and "
                      "the build with its tests in four configurations")
    parser.add_argument("--framework-include", metavar="DIR",
                        help="with --full, for build/build.py (default: "
                        "$PHI_FRAMEWORK_INCLUDE)")
    parser.add_argument("--without-framework", action="store_true",
                        help="with --full, for build/build.py")
    args = parser.parse_args(argv)
    tools = find_tools(REPO)
    start = time.monotonic()
    if args.full:
        if args.without_framework:
            build_args = ["--without-framework"]
        elif args.framework_include or environ.get("PHI_FRAMEWORK_INCLUDE"):
            build_args = ["--framework-include", args.framework_include
                          or environ["PHI_FRAMEWORK_INCLUDE"]]
        else:
            print("check: --full builds the core, and the framework ABI "
                  "header is not in this repository.\nPass "
                  "--framework-include DIR (or set PHI_FRAMEWORK_INCLUDE), "
                  "or --without-framework. See docs/BUILD.md.")
            return 1
        tier, failed = "full", check_full(REPO, tools, build_args)
    elif args.framework_include or args.without_framework:
        parser.error("the framework options go with --full")
    elif args.tidy:
        tier, failed = "tidy", run_checks("tidy", [TIDY], REPO, tools)
    else:
        tier, failed = "fast", check_fast(REPO, tools)
    if failed:
        print("check: %s FAILED: %s" % (tier, ", ".join(failed)))
        return 1
    print("check: %s ok (%.1fs)" % (tier, time.monotonic() - start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
