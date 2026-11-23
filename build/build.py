#!/usr/bin/env python3
# Copyright 2026 Phinomenon Inc.
# Licensed under the Apache License, Version 2.0; see the LICENSE file.
"""Builds and tests the shared client core on Linux.

  build/build.py --framework-include DIR   every step, tests run once
  build/build.py --without-framework       the same, minus the one source
                                           that calls the framework
  ... --repeat 5      run each test binary five times
  ... --cxx g++       the other supported compiler
  ... --std c++23     the standard the Windows build asks for
  ... --no-tests      stop after the compile and link checks

The framework ABI header is not part of this repository: the consumer
supplies it. DIR is an include root that holds
chrome/browser/phinomenon/phi_framework/phi_framework_api.h, for example a
phibrowser-win checkout's client/compat or a framework checkout's src. The
environment variable PHI_FRAMEWORK_INCLUDE is the option's default. Only
that phi_framework directory is made visible to the compiler.

Steps, all of them fatal on failure:

  source   tools/resync.py verify: the imported files still match
           SOURCE.json byte for byte.
  compile  every core .cc, every core header on its own and every .cc of
           port/ (the code written here), with the product settings
           (CORE_FLAGS) and -Wall -Wextra -Werror.
  vendor   the vendored SQLite and BoringSSL, the way the Windows build
           compiles them (warnings off: not our code).
  link     links every core and port object with SQLite and BoringSSL and
           compares the undefined symbols with build/link-closure.expected
           (empty), so a re-sync that adds a dependency is noticed.
  tests    the tests of port/tests under ASan + UBSan.

Needs python3, git, ar and one of clang++ / g++ (with the matching C
compiler); nothing is installed and nothing outside --out is written, apart
from the tests' own temporary files, which go to <out>/tmp.
"""

import argparse
import concurrent.futures
import hashlib
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(REPO, "core", "client")
COMPAT = os.path.join(CLIENT, "compat")
BORINGSSL = os.path.join(REPO, "third_party", "boringssl")
SQLITE = os.path.join(REPO, "third_party", "sqlite", "sqlite3.c")
# Code written in this repository (not imported): what the core needs on
# Linux and upstream keeps in Windows-only files.
PORT = os.path.join(REPO, "port")

FRAMEWORK_DIR = "chrome/browser/phinomenon/phi_framework"
FRAMEWORK_HEADER = FRAMEWORK_DIR + "/phi_framework_api.h"

C_COMPILER = {"clang++": "clang", "g++": "gcc"}

# The Windows build is /std:c++23preview /GR- -D_HAS_EXCEPTIONS=0 -DNDEBUG
# (phibrowser-win build-client.py). The core uses nothing newer than C++20,
# which is what the Linux client compiles with, so C++20 is the default and
# keeps a C++23 dependency from slipping in; --std c++23 checks the other.
#
# __fastfail is an MSVC intrinsic that compat/base/containers/span.h:62 and
# compat/values.cc:424,436 call outside any _WIN32 guard. The files are
# imported unchanged, so the build supplies the name instead.
CORE_FLAGS = ["-fno-exceptions", "-fno-rtti", "-DNDEBUG",
              "-D__fastfail(x)=__builtin_trap()"]
WARNINGS = ["-Wall", "-Wextra", "-Werror"]
# No warning class is disabled. compat/ and BoringSSL's include root are
# SYSTEM include roots, and so is the consumer's framework include root, as
# the upstream host-test recipe had it: the framework ABI header has 108
# no-op default method bodies that trip -Wunused-parameter (docs/BUILD.md).
INCLUDES = ["-I", os.path.join(REPO, "core"), "-I", REPO,
            "-isystem", COMPAT,
            "-isystem", os.path.join(BORINGSSL, "src", "include")]
SANITIZE = ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
            "-fno-omit-frame-pointer", "-g", "-O1"]
# phibrowser-win build-client.py SQLITE_DEFINES.
SQLITE_DEFINES = [
    "-DSQLITE_THREADSAFE=1", "-DSQLITE_DQS=0", "-DSQLITE_DEFAULT_MEMSTATUS=0",
    "-DSQLITE_DEFAULT_WAL_SYNCHRONOUS=1", "-DSQLITE_OMIT_DEPRECATED",
    "-DSQLITE_OMIT_LOAD_EXTENSION", "-DSQLITE_OMIT_SHARED_CACHE",
    "-DSQLITE_OMIT_PROGRESS_CALLBACK", "-DSQLITE_USE_ALLOCA"]
# phibrowser-win build-client.py: /TP /std:c++17 /GR- and these defines.
BORINGSSL_FLAGS = ["-x", "c++", "-std=c++17", "-fno-rtti", "-O2", "-w",
                   "-I", os.path.join(BORINGSSL, "src"),
                   "-I", os.path.join(BORINGSSL, "src", "include"),
                   "-DOPENSSL_NO_ASM", "-DBORINGSSL_IMPLEMENTATION"]

# The tests of port/, in port/tests, as (test, sources it links, vendored
# libraries, arguments, last line). The sources are relative to port/. Tests
# and sources are compiled with the product settings (CORE_FLAGS) and the
# sanitizers. The last element is a regular expression that the last line a
# passing run prints must match in full: exit status 0 alone is not a pass,
# a binary that ran nothing would have it too.
PORT_TESTS = [
    ("base_test", ["base/base64.cc", "base/string_number_conversions.cc"],
     ["crypto"], [], r"base_test: [1-9]\d* checks passed"),
    ("store_files_test", ["store/phi_store_files.cc",
                          "../core/client/phi_local_store.cc",
                          "../core/client/phi_url_rule_normalize.cc"],
     ["sqlite"], [], r"store_files_test: [1-9]\d* checks passed"),
    ("store_usable_test", ["store/phi_store_files.cc",
                           "../core/client/phi_local_store.cc",
                           "../core/client/phi_url_rule_normalize.cc"],
     ["sqlite"], [], r"store_usable_test: [1-9]\d* checks passed"),
]


class Failure(Exception):
    pass


def core_sources():
    """(sources, headers) of the core, relative to core/client."""
    sources, headers = [], []
    for base, _, names in os.walk(CLIENT):
        for name in sorted(names):
            path = os.path.relpath(os.path.join(base, name), CLIENT)
            if name.endswith(".cc"):
                sources.append(path)
            elif name.endswith(".h") and not path.startswith("compat"):
                headers.append(path)
    return sorted(sources), sorted(headers)


def port_sources():
    """The sources of port/ outside port/tests, relative to port/."""
    sources = []
    for base, _, names in os.walk(PORT):
        for name in sorted(names):
            path = os.path.relpath(os.path.join(base, name), PORT)
            if name.endswith(".cc") and path.split(os.sep)[0] != "tests":
                sources.append(path)
    return sorted(sources)


def needs_framework(path, client=CLIENT, seen=None):
    """True when core file |path| includes a framework header, directly or
    through other core files (a textual scan; no core include is guarded)."""
    seen = set() if seen is None else seen
    if path in seen:
        return False
    seen.add(path)
    try:
        with open(os.path.join(client, path), encoding="utf-8") as f:
            names = re.findall(r'^\s*#\s*include\s+"([^"]+)"', f.read(), re.M)
    except OSError:
        return False
    return any(
        name.startswith("chrome/") or (
            name.startswith("client/")
            and needs_framework(name[len("client/"):], client, seen))
        for name in names)


def resolve_framework(root, out):
    """An include root under |out| that holds only |root|'s phi_framework
    directory, so no other header of a framework checkout (its base/, its
    third_party/) can be picked up by accident."""
    source = os.path.realpath(os.path.join(root, *FRAMEWORK_DIR.split("/")))
    if not os.path.isfile(os.path.join(root, *FRAMEWORK_HEADER.split("/"))):
        raise Failure("--framework-include %s: no %s under it" % (
            root, FRAMEWORK_HEADER))
    shim = os.path.join(out, "framework-include",
                        hashlib.sha256(source.encode()).hexdigest()[:12])
    link = os.path.join(shim, *FRAMEWORK_DIR.split("/"))
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if not os.path.islink(link):
        os.symlink(source, link)
    return shim


def object_name(path):
    return path.replace(os.sep, "_") + ".o"


def up_to_date(command, output):
    """True when |output| was built by |command| from unchanged inputs."""
    try:
        with open(output + ".cmd") as f:
            if f.read() != " ".join(command):
                return False
        built = os.path.getmtime(output)
        with open(output + ".d") as f:
            deps = f.read().replace("\\\n", " ").split(":", 1)[1].split()
        return all(os.path.getmtime(dep) <= built for dep in deps)
    except (OSError, IndexError):
        return False


def compile_one(job):
    """Runs one compile; returns (label, error text or None)."""
    label, command, output = job
    command = command + ["-MD", "-MF", output + ".d", "-o", output]
    if up_to_date(command, output):
        return label, None
    os.makedirs(os.path.dirname(output), exist_ok=True)
    result = subprocess.run(command, capture_output=True, text=True,
                            errors="replace")
    if result.returncode or result.stderr.strip():
        if os.path.exists(output):
            os.remove(output)
        return label, " ".join(command) + "\n" + result.stderr
    with open(output + ".cmd", "w") as f:
        f.write(" ".join(command))
    return label, None


def run_jobs(step, jobs, workers):
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(compile_one, jobs))
    failed = [(label, text) for label, text in results if text]
    for label, text in failed:
        print("FAILED %s\n%s" % (label, text))
    print("%-8s %d/%d ok (%.1fs)" % (step, len(jobs) - len(failed), len(jobs),
                                     time.monotonic() - start))
    if failed:
        raise Failure(step)


def step_source():
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "tools", "resync.py"), "verify"],
        capture_output=True, text=True)
    print("source   " + result.stdout.strip().splitlines()[-1])
    if result.returncode:
        print(result.stdout)
        raise Failure("source")


def step_compile(args, out, framework):
    """Product-flag objects of every core and port source; core headers on
    their own."""
    flags = [args.cxx, "-std=" + args.std, *CORE_FLAGS, "-O2", *WARNINGS,
             *INCLUDES, *framework, "-c"]
    sources, headers = core_sources()
    if not framework:
        skipped = [p for p in sources + headers if needs_framework(p)]
        print("compile  without the framework header, skipping: "
              + ", ".join(skipped))
        sources = [p for p in sources if p not in skipped]
        headers = [p for p in headers if p not in skipped]
    jobs, objects = [], []
    for source in sources:
        output = os.path.join(out, "core", object_name(source))
        objects.append(output)
        jobs.append((source, flags + [os.path.join(CLIENT, source)], output))
    for source in port_sources():
        output = os.path.join(out, "port", object_name(source))
        objects.append(output)
        jobs.append(("port/" + source, flags + [os.path.join(PORT, source)],
                     output))
    for header in headers:
        unit = os.path.join(out, "headers",
                            header.replace(os.sep, "_") + ".cc")
        os.makedirs(os.path.dirname(unit), exist_ok=True)
        text = '#include "client/%s"\n' % header
        if not os.path.exists(unit) or open(unit).read() != text:
            with open(unit, "w") as f:
                f.write(text)
        jobs.append((header, flags + [unit], unit + ".o"))
    run_jobs("compile", jobs, args.jobs)
    return objects


def step_vendor(args, out):
    """{name: {"plain": [link inputs], "san": [link inputs]}}."""
    cc = C_COMPILER[os.path.basename(args.cxx)]
    with open(os.path.join(BORINGSSL, "sources.txt")) as f:
        crypto_sources = f.read().split()
    jobs, crypto_objects = [], []
    for relative in crypto_sources:
        output = os.path.join(out, "boringssl", object_name(relative))
        crypto_objects.append(output)
        jobs.append((relative, [args.cxx, *BORINGSSL_FLAGS, "-c",
                                os.path.join(BORINGSSL, "src", relative)],
                     output))
    sqlite = {"plain": os.path.join(out, "sqlite", "sqlite3.o"),
              "san": os.path.join(out, "sqlite", "sqlite3.san.o")}
    jobs.append(("sqlite3.c", [cc, "-O2", "-w", *SQLITE_DEFINES, "-c", SQLITE],
                 sqlite["plain"]))
    jobs.append(("sqlite3.c (sanitizers)",
                 [cc, "-w", *SANITIZE, *SQLITE_DEFINES, "-c", SQLITE],
                 sqlite["san"]))
    run_jobs("vendor", jobs, args.jobs)
    archive = os.path.join(out, "boringssl", "libcrypto.a")
    newest = max(os.path.getmtime(o) for o in crypto_objects)
    if not os.path.exists(archive) or os.path.getmtime(archive) < newest:
        if os.path.exists(archive):
            os.remove(archive)
        subprocess.run(["ar", "rcs", archive, *crypto_objects], check=True)
    system = ["-lpthread", "-ldl"]
    return {"crypto": {"plain": [archive] + system, "san": [archive] + system},
            "sqlite": {"plain": [sqlite["plain"]] + system,
                       "san": [sqlite["san"]] + system}}


def read_expected():
    path = os.path.join(REPO, "build", "link-closure.expected")
    with open(path) as f:
        return sorted(line.strip() for line in f
                      if line.strip() and not line.startswith("#"))


def undefined_symbols(linker_output):
    """The symbols a failed link reports as undefined, sorted. GNU ld and
    gold write "undefined reference to `name'" (gold with two straight
    quotes), lld "undefined symbol: name"."""
    found = re.findall(r"undefined reference to [`']([^'\n]+)'"
                       r"|undefined symbol: ([^\n]+)", linker_output)
    return sorted({bfd or lld for bfd, lld in found})


def step_link(args, out, objects, vendor, expected=None):
    """The undefined symbols of |objects| must be exactly |expected|
    (default: the recorded ones)."""
    stub = os.path.join(out, "link", "main.cc")
    os.makedirs(os.path.dirname(stub), exist_ok=True)
    with open(stub, "w") as f:
        f.write("int main() { return 0; }\n")
    result = subprocess.run(
        [args.cxx, stub, *objects, *vendor["sqlite"]["plain"],
         *vendor["crypto"]["plain"], "-o", os.path.join(out, "link", "all")],
        capture_output=True, text=True, errors="replace")
    found = undefined_symbols(result.stderr)
    if expected is None:
        expected = read_expected()
    print("link     %d undefined symbol(s), %d expected" % (
        len(found), len(expected)))
    if found != expected or (result.returncode and not found):
        for name in sorted(set(found) - set(expected)):
            print("  new undefined symbol: " + name)
        for name in sorted(set(expected) - set(found)):
            print("  no longer undefined: " + name)
        if not found:
            print(result.stderr)
        raise Failure("link")


def test_tables():
    """(table, source root, test directory) for each set of tests."""
    return [(PORT_TESTS, PORT, os.path.join(PORT, "tests"))]


def step_tests(args, out, vendor, tables=None):
    """Builds every test of |tables| (default: test_tables()) under ASan +
    UBSan; returns the binaries."""
    core_flags = [args.cxx, "-std=" + args.std, *CORE_FLAGS, *SANITIZE,
                  *WARNINGS, *INCLUDES, "-c"]
    jobs = {}
    links = []
    plan = [(root, directory, *entry)
            for table, root, directory in tables or test_tables()
            for entry in table]
    for (root, directory, test, sources, libraries, arguments,
         pattern) in plan:
        inputs = []
        for source in sources:
            path = os.path.relpath(os.path.join(root, source), REPO)
            output = os.path.join(out, "san", object_name(path))
            jobs[output] = (path + " (sanitizers)",
                            core_flags + [os.path.join(REPO, path)], output)
            inputs.append(output)
        output = os.path.join(out, "tests", test + ".o")
        jobs[output] = (test + ".cc", core_flags
                        + [os.path.join(directory, test + ".cc")], output)
        inputs.append(output)
        for library in libraries:
            inputs += vendor[library]["san"]
        links.append((test, inputs, arguments, pattern))
    run_jobs("tests", list(jobs.values()), args.jobs)
    binaries = []
    for test, inputs, arguments, pattern in links:
        binary = os.path.join(out, "tests", test)
        result = subprocess.run(
            [args.cxx, *SANITIZE, *inputs, "-o", binary],
            capture_output=True, text=True, errors="replace")
        if result.returncode:
            print("FAILED link %s\n%s" % (test, result.stderr))
            raise Failure("tests")
        binaries.append((test, binary, arguments, pattern))
    return binaries


def step_run(args, out, binaries):
    tmp = os.path.join(out, "tmp")
    os.makedirs(tmp, exist_ok=True)
    env = dict(os.environ, TMPDIR=tmp,
               ASAN_OPTIONS="detect_leaks=1:abort_on_error=0",
               UBSAN_OPTIONS="print_stacktrace=1:halt_on_error=1")
    failed = False
    for test, binary, arguments, pattern in binaries:
        passed, seconds, last = 0, [], ""
        for _ in range(args.repeat):
            start = time.monotonic()
            result = subprocess.run([binary, *arguments], cwd=tmp, env=env,
                                    capture_output=True, text=True,
                                    errors="replace", timeout=900)
            seconds.append(time.monotonic() - start)
            lines = (result.stdout + result.stderr).strip().splitlines()
            last = lines[-1] if lines else ""
            if result.returncode == 0 and re.fullmatch(pattern, last):
                passed += 1
            elif result.returncode == 0:
                failed = True
                print('FAILED %s: exit 0, but its last line "%s" is not '
                      'the line of a passing run (%s)' % (test, last, pattern))
            else:
                failed = True
                print("FAILED %s (exit %s)\n%s" % (
                    test, result.returncode, result.stdout + result.stderr))
        print("  %-24s %d/%d passed  %.2fs avg  %.2fs max  | %s" % (
            test, passed, args.repeat, sum(seconds) / len(seconds),
            max(seconds), last))
    if failed:
        raise Failure("run")


def main(argv=None, environ=os.environ):
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        epilog="See docs/BUILD.md.")
    parser.add_argument("--cxx", default="clang++",
                        choices=sorted(C_COMPILER),
                        help="C++ compiler (default: clang++)")
    parser.add_argument("--std", default="c++20", choices=["c++20", "c++23"])
    parser.add_argument("--framework-include", metavar="DIR",
                        default=environ.get("PHI_FRAMEWORK_INCLUDE"),
                        help="include root holding " + FRAMEWORK_HEADER
                        + " (default: $PHI_FRAMEWORK_INCLUDE)")
    parser.add_argument("--without-framework", action="store_true",
                        help="skip the sources that include the framework "
                        "header")
    parser.add_argument("--out", help="build directory, one per compiler "
                        "and standard (default: out/<cxx>-<std>)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--repeat", type=int, default=1,
                        help="how many times each test binary runs")
    parser.add_argument("--no-tests", action="store_true")
    args = parser.parse_args(argv)
    if not args.framework_include and not args.without_framework:
        print("The framework ABI header (%s) is not in this repository.\n"
              "Pass --framework-include DIR (or set PHI_FRAMEWORK_INCLUDE) "
              "with the include root that holds it,\nor --without-framework "
              "to build everything that does not need it. See docs/BUILD.md."
              % FRAMEWORK_HEADER)
        return 1
    out = os.path.abspath(args.out or os.path.join(
        REPO, "out", "%s-%s" % (args.cxx, args.std)))
    start = time.monotonic()
    try:
        step_source()
        framework = []
        if not args.without_framework:
            framework = ["-isystem",
                         resolve_framework(args.framework_include, out)]
        objects = step_compile(args, out, framework)
        vendor = step_vendor(args, out)
        step_link(args, out, objects, vendor)
        if not args.no_tests:
            step_run(args, out, step_tests(args, out, vendor))
    except Failure as failure:
        print("FAILED at step: %s" % failure)
        return 1
    print("ok (%s, -std=%s, %s, %.1fs)" % (
        args.cxx, args.std,
        "without framework" if args.without_framework
        else "framework header from " + args.framework_include,
        time.monotonic() - start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
