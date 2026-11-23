# Build and quality rules

The core has its own rule set, separate from both clients: compile, strict
warnings, and the tests under ASan + UBSan. Format, ASCII and clang-tidy
style checks are not run on the files that came from the Windows client
(`core/`, `third_party/`): they keep the style they arrived in. `core/` is
edited here all the same (`README.md`, "Who edits what"), and whether new
code there should meet these checks is not settled.
Files written in this repository
(`port/`, `build/`, `tools/`, `docs/`, `README.md`, `SOURCE.md`) are English
ASCII, and they do get the style checks: `tools/check.py`, described in
`CHECKS.md`, whose full mode also runs this build in four
configurations. The code under `port/` is compiled with the same settings as the core
(`-fno-exceptions -fno-rtti -Wall -Wextra -Werror`), and so are its tests,
which run under ASan + UBSan with the others.

## Running it

```sh
export PHI_FRAMEWORK_INCLUDE=/path/to/include-root   # see the next section
python3 build/build.py               # clang++, -std=c++20, tests once
python3 build/build.py --repeat 5    # each test binary five times
python3 build/build.py --cxx g++
python3 build/build.py --std c++23
python3 build/build.py --without-framework   # no framework header at hand
python3 tools/resync_test.py         # the re-sync tool's unit tests
python3 build/build_test.py          # the build script's unit tests
python3 tools/check.py --full        # the style checks, both unit-test
                                     # files and this build in all four
                                     # compiler and standard combinations
```

Needs python3 (standard library only), git, ar and clang++ with clang or
g++ with gcc. No CMake, no GN, no package to install. Output goes to
`out/<compiler>-<std>/`; the tests' temporary files go to `out/.../tmp`.
A full build from nothing takes about 45 s on a large machine (BoringSSL is
244 files); an unchanged tree rebuilds in about 3 s.

## The framework ABI header comes from the consumer

One translation unit, `core/client/phi_chromium_sync.cc`, includes
`chrome/browser/phinomenon/phi_framework/phi_framework_api.h` (which
includes `phi_ids.h` beside it). No other source and no header of the core
does; `phi_chromium_sync.h` only forward-declares the class. The header is
the contract with the framework, and a client has to compile against the
version of the framework it runs with, so this repository does not carry a
copy. The build takes an include root:

```sh
python3 build/build.py --framework-include DIR
# or: PHI_FRAMEWORK_INCLUDE=DIR python3 build/build.py
```

`DIR` is any directory under which
`chrome/browser/phinomenon/phi_framework/phi_framework_api.h` exists. Two
that work as they are, with no shim and no copy:

| `DIR` | ABI version | Use |
|---|---|---|
| `<phibrowser-win checkout>/client/compat` | the one that checkout is pinned to (9 at `7348b4a`, 10 at `2931e0e`) | what the Windows client compiles against |
| `<framework checkout>/src` | that checkout's (10 on the Linux branch in November 2026) | what the Linux client compiles against |

The spelling the core uses resolves against both directly. The build does
not pass `DIR` itself to the compiler: it links only `DIR`'s `phi_framework`
directory into `out/.../framework-include/<hash>/` and uses that as a
system include root. A framework checkout also has `base/`, `third_party/`
and everything else of Chromium under the same root, and a core include
that compat does not cover must fail, not be answered by Chromium.

Without the option the build stops before doing anything and names it.
`--without-framework` builds and tests everything except
`phi_chromium_sync.cc` (it says so in its output); no test needs that file.

Checked on 2026-11-21 with three headers (the Windows copy at `7348b4a`,
ABI 9; the Windows copy at `2931e0e`, ABI 10; the Linux framework's, ABI
10), each in all four compiler and standard configurations: 43 of 43
translation units compile, the same five symbols stay undefined (this was
before `port/base/` defined them), the tests of that day (then including
the six imported host tests, since removed) pass five times out of five. The object file of
`phi_chromium_sync.cc` is byte-identical for all three (ABI 10 appends one
method at the end of the interface; the three methods the file calls keep
their slots). The two ABI 10 headers differ only in their copyright lines.

## Steps

| Step | What it does | Fails when |
|---|---|---|
| source | `tools/resync.py verify` | a file `SOURCE.json` lists is missing, a vendored file (`third_party/`) differs from its recorded checksum, or an unlisted file sits in `core/` or `third_party/` |
| compile | the 21 core `.cc` files, each of the 22 core headers on its own (20 `.cc` with `--without-framework`) and the `.cc` files of `port/` outside `port/tests/` | any error or warning |
| vendor | SQLite (plain and sanitized) and BoringSSL's 244 files into `libcrypto.a` | any error |
| link | links all core and `port/` objects with SQLite and BoringSSL | the undefined symbols differ from `build/link-closure.expected` (which is empty), or the link fails for any other reason |
| tests | builds the tests of `port/` (`PORT_TESTS`) and runs each `--repeat` times | any error, warning, failed check or sanitizer report, or a run that exits 0 without the last line of a passing run (`TESTS.md`) |

## Compiler and settings

Default compiler: `clang++` (Ubuntu clang 18.1.3 here), the compiler the
Linux client is built with. `g++` (13.3.0 here) is the supported alternative. Both link
libstdc++.

Default standard: `-std=c++20`. The Windows client builds with `/std:c++23preview`,
but the trial compile of 2026-11-21 found that the code uses nothing newer
than C++20 and does not compile as C++17, and the Linux client is a C++20
build. Building as C++20 keeps a C++23 dependency from arriving unnoticed in
a change made for Windows. `--std c++23` builds the Windows setting.

Core objects (`CORE_FLAGS` in `build/build.py`):

| Flag | Upstream equivalent (phibrowser-win `build-client.py`) |
|---|---|
| `-fno-exceptions` | `-D_HAS_EXCEPTIONS=0` |
| `-fno-rtti` | `/GR-` |
| `-DNDEBUG` | `-DNDEBUG` |
| `-O2` (compile step), `-O1 -g` (test objects) | `/O2` |
| `-D'__fastfail(x)=__builtin_trap()'` | none, see below |
| `-Wall -Wextra -Werror` | none: upstream compiles with warnings off |

Test sources are compiled with `-Wall -Wextra -Werror` and the sanitizers but
without `-fno-exceptions -fno-rtti -DNDEBUG`, as upstream's `run.sh` did.

Sanitizers: `-fsanitize=address,undefined -fno-sanitize-recover=undefined`,
on the core objects a test links, the test itself and SQLite; leak detection
on. BoringSSL is linked unsanitized.

Vendored code is compiled the way upstream compiles it, warnings off:
SQLite as C with upstream's nine `SQLITE_*` defines; BoringSSL's
`sources.txt` as C++17 with `-fno-rtti -DOPENSSL_NO_ASM
-DBORINGSSL_IMPLEMENTATION`.

## The `__fastfail` define

`__fastfail` is an MSVC intrinsic. Three places in the core call it outside any
`_WIN32` guard: `core/client/compat/base/containers/span.h:62` and
`core/client/compat/values.cc:424` and `:436`. Without the define three
translation units fail (`phi_tab_store.cc`, `compat/json.cc`,
`compat/values.cc`):

```
core/client/compat/base/containers/span.h:62:7: error: use of undeclared identifier '__fastfail'
```

The build defines the name as `__builtin_trap()`, which keeps the meaning
(terminate at once). It dates from when these files were copies that could
not be edited here. The fix is a guard in the two files, which can be made
here now and has to leave the Windows build as it is; with it, delete the
define.

## Warnings: nothing is disabled

No `-Wno-...` flag is used, for either compiler. One decision stands in for
it and is recorded here: `core/client/compat`, BoringSSL's `include` and
the consumer's framework include root are passed as `-isystem` roots, so
warnings whose location is in a header found through them are not reported.

Evidence (2026-11-21, measured while the ABI 9 header copy was still under
compat, with `-I` in place of `-isystem`, all 43 translation units,
`-Wall -Wextra`): clang++ and g++ each report 108 warnings, all
`-Wunused-parameter`, all in `phi_framework_api.h` (the framework ABI
header: interface methods with empty default bodies). Nothing else in
compat warns; `compat/json.cc` and `compat/values.cc` are
compiled as ordinary strict translation units and are clean. Upstream's own
host-test recipe did the same thing for the same header (its `run.sh`
comment: "a SYSTEM root, so the framework header's no-op delegate bodies do
not trip -Wextra").

The 42 files of the two components themselves produce no warning under
either compiler at `-O2` or `-O1`.

## What the link step says

Linking every core object with the objects of `port/` leaves no undefined
symbol, and `build/link-closure.expected` is empty. With `--without-framework`
it is the same: `phi_chromium_sync.cc` is left out and nothing else refers to
it.

The core's objects alone lack five symbols: `base::Base64Encode`,
`base::Base64Decode` (used by `phi_tab_store.cc`) and `base::NumberToString`
for `int`, `long` and `double` (used by `compat/json.cc`). Upstream defines
them in `client/compat/base.cc`, which includes `<windows.h>` and is not
imported. `port/base/` defines them, with upstream's results
(`docs/TESTS.md`). The `long` overload is what `int64_t` resolves to on
Linux; on Windows the same call reaches the `long long` one.

The check stays: when a change makes the core call something that nothing
here defines, the step prints `new undefined symbol: ...` and fails. Adding
the name to the expected list is not the fix.

The step reads the symbol names from the linker's messages and knows the
wording of GNU ld, gold and lld. With any other linker a failed link still
fails the step, with the linker's output and without the list of names. The
names in the expected file are spelled as GNU ld prints them for libstdc++;
the file is empty, so this matters only if a name is ever added.
