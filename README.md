# phi-client-core

The part of the Phi Browser client that the desktop clients can share: the
sync engine and the account's local store. The code is the Windows client's,
copied without changes, with a build that proves it compiles, links and its
tests pass on Linux, and with the few pieces written here that it needs
on Linux (`port/`).

Status: a local repository, step 1 of the split. Linux is to consume it
first; the Windows client is to switch to it later. Nothing consumes it yet.

## Where the code comes from

Every file under `core/` and `third_party/`, and `LICENSE`, is a
byte-identical copy from https://github.com/phibrowser/phibrowser-win
(Apache-2.0):

- `core/` and `third_party/`: commit `2931e0e` (2026-11-20).

Upstream's host tests were imported once and removed on 2026-11-23, before
this repository was published (`docs/TESTS.md`).

`SOURCE.md` has the exact commits, the path mapping, how the file list was
derived, and every file with its checksum and role.

## The move-only rule

Files that came from the Windows client are never edited here: no fixes, no
reformatting, no Linux adaptation. A change they need is made in the Windows
repository and arrives through a re-sync. `python3 tools/resync.py verify`
checks this and is the first step of every build. Whatever is written here
lives in `port/` (code), `build/`, `tools/` and `docs/`.

## Layout

| Path | Contents | Origin |
|---|---|---|
| `core/client/` | the sync engine and the local store (42 files, 26,118 lines) | Windows `client/` |
| `core/client/compat/` | the `base/` stand-ins they include (10 files) | Windows `client/compat/` |
| `third_party/sqlite/` | SQLite 3.53.4 amalgamation | Windows `third_party/sqlite/` |
| `third_party/boringssl/` | BoringSSL, crypto only | Windows `third_party/boringssl/` |
| `LICENSE` | Apache-2.0 | Windows `LICENSE` |
| `port/base/` | the five `base::` functions upstream defines in its Windows-only `client/compat/base.cc` | written here |
| `port/store/` | the Linux side of the account local store: the durable manifest write, the account directory tree under a root the host names, `OpenAccountStore` | written here |
| `port/tests/` | the tests of `port/` | written here |
| `build/` | `build.py`, the expected link closure | written here |
| `tools/` | `resync.py`, `check.py` (the checks for the code written here), `install-hooks.py`, and their tests | written here |
| `.clang-format`, `.clang-tidy` | the rules `check.py` applies to `port/` | written here (the Linux client's rules) |
| `docs/`, `README.md`, `SOURCE.md`, `SOURCE.json` | documentation and the manifest | written here |

Sync engine: `phi_sync_engine`, `phi_sync_rules`, `phi_sync_keys`,
`phi_sync_bookmarks`, `phi_sync_owned`, `phi_sync_pins`, `phi_sync_proto`,
`phi_sync_owned_kinds`, `phi_sync_protocol`, `phi_sync_spaces`,
`phi_key_crypto`, `phi_sync_invalidation`, `phi_sync_merge`,
`phi_sync_rank`, `phi_chromium_sync`, `phi_sync_status`, `phi_sync_clock`,
`phi_sync_cleanup`.

Account local store: `phi_local_store` (with `phi_unicode_lower.inc`),
`phi_guest_migration`, `phi_tab_store`, `phi_url_rule_normalize`.

## Build and test

```sh
python3 build/build.py --framework-include DIR --repeat 3
```

`DIR` is the include root that holds the framework ABI header
(`chrome/browser/phinomenon/phi_framework/phi_framework_api.h`): a
phibrowser-win checkout's `client/compat`, or a framework checkout's `src`.
The header is the consumer's to supply, in the version of the framework it
runs with; this repository carries no copy. `--without-framework` builds
everything except the one source that needs it (`phi_chromium_sync.cc`).

Python 3, git, ar and clang++ (default) or g++; nothing to install. It
verifies the import, compiles the core and `port/` with `-std=c++20
-fno-exceptions -fno-rtti -Wall -Wextra -Werror`, builds the vendored SQLite
and BoringSSL, checks that everything links with no symbol left undefined
and runs the tests under ASan + UBSan. Details,
options and the reasoning behind every setting: `docs/BUILD.md`. What the tests
cover and do not: `docs/TESTS.md`.

## Checks for the code written here

```sh
python3 tools/install-hooks.py                           # once per clone
python3 tools/check.py --fast                            # the pre-commit hook runs this
python3 tools/check.py --full --framework-include DIR    # before review
```

The fast check verifies the import and, for the files written here only,
the copyright header, clang-format, ASCII, pyflakes and the script tests.
The full check adds clang-tidy and the build above in four configurations
(clang++ and g++, C++20 and C++23). Imported files are never formatted or
linted. Do not commit with `--no-verify`. Details: `docs/CHECKS.md`.

## Re-sync from the Windows client

```sh
python3 tools/resync.py sync --upstream /path/to/phibrowser-win --commit <rev>           # report
python3 tools/resync.py sync --upstream /path/to/phibrowser-win --commit <rev> --apply   # refresh
python3 build/build.py --framework-include DIR --repeat 3
```

The full procedure is in `SOURCE.md`.

## What is deliberately not here yet

- **The framework ABI header.** Supplied by the consumer at build time
  (above).
- **Implementations of the host seams.** The engine reaches its host through
  `SettingsSidecars` and `SyncableSetting` (`phi_sync_engine.h`),
  `SyncTransport` (`phi_sync_protocol.h`) and the per-kind local-access
  interfaces. The Windows implementations (`phi_sync_*_win`,
  `phi_sync_key_service`, `phi_key_api_win`, HTTP, key protection) are
  Windows code and stay there; Linux ones do not exist.
- **The rest of `base::`.** `port/base/` defines the five functions the
  core's objects need (`Base64Encode` and `Base64Decode` for
  `std::string_view`, `NumberToString` for `int`, `long`, `double`) and
  nothing else the compat headers declare. Upstream's definitions are in
  `client/compat/base.cc`, which is Windows-only.
- **A product-grade non-Windows branch in `phi_local_store.cc`.** Its
  compatibility-manifest read and write outside `_WIN32` (lines 125-147) is
  the host-test stand-in: no flush to disk. It stays; `port/store/`'s
  `PrepareManifest` writes the manifest durably before `Open` for every
  state the stand-in would act on, so a host that opens through
  `OpenAccountStore` does not reach it.
- **Tests that run the sync engine proper.** Upstream's `sync_engine_test`
  and `sync_keys_test` no longer compiled against today's interfaces, and
  no upstream host test is kept here (`docs/TESTS.md`).
- **The rest of the Windows client's platform-neutral code** (window and
  sidebar models, theme, palette, auth codec and others), and zlib.
- **A remote, CI, and a consumer.** Neither client builds against this
  repository yet.

## Licence

Apache-2.0, see `LICENSE` (the Windows client's licence file, unchanged).
It covers the imported files and the files written here (`port/`, `build/`,
`tools/`, `docs/`, this file, `SOURCE.md`, `SOURCE.json`) alike.
`third_party/boringssl/src/LICENSE` and
`third_party/boringssl/src/third_party/fiat/LICENSE` cover BoringSSL;
SQLite is in the public domain (`third_party/sqlite/README.phi.md`).
