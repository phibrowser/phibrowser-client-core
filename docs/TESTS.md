# Tests

The only tests in this repository are the tests of the code written here,
`port/tests/` (the last section).

## Upstream's host tests were removed

This repository first imported upstream's host tests (six tests, `json_lite.h`
and the Mac-written sync vectors) unchanged from the Windows client at
commit `b5cb6c4` (2026-10-16). On 2026-11-23, before the repository was
published, they were removed: upstream had removed them from its public
repository one commit later as internal tooling, and three of the files
carried a copyright header that is not this repository's Apache-2.0 one.
They can still be found in upstream's history (`b5cb6c4`) and in this
repository's history up to the commit that removed them.

Consequence: these components have no test in this repository any more.
`core/` is edited here now (`README.md`, "Who edits what"), so a test for
any of them is new code written here, beside the tests of `port/`.

- the sync merge core, ranks, clock, client tag hash and URL rule
  normalisation, and the Mac-written sync vectors that checked them;
- the account local store, apart from what `port/tests/` covers (the
  durable write, the account directories, opening, writing and reading back
  the guest account's store, and the manifest states);
- Guest to account migration;
- the sync status summary;
- the key-layer primitives (HPKE, envelopes, recovery key);
- the invalidation stream parser.

The sync engine proper (`phi_sync_engine.cc`, `phi_sync_rules.cc`,
`phi_sync_keys.cc`, `phi_sync_bookmarks.cc`, `phi_sync_pins.cc`,
`phi_sync_owned.cc`, `phi_sync_owned_kinds.cc`, `phi_sync_protocol.cc`,
`phi_sync_spaces.cc`) was compiled but not executed by any test before this
as well: upstream's `sync_engine_test` and `sync_keys_test` do not compile
against today's interfaces. `phi_tab_store.cc` and `phi_chromium_sync.cc`
never had a host test.

## A pass needs its last line

Exit status 0 is not enough for a pass: a binary that ran nothing would
have it too. Each entry of `PORT_TESTS` in `build/build.py` carries a
regular expression that the last line a run prints must match, and a run
that exits 0 without it fails the build. The tests written here count their
checks, and zero is refused.

## Tests of `port/`

`port/tests/base_test.cc` covers `port/base/` through the imported compat
headers (`base/base64.h`, `base/strings/string_number_conversions.h`); it
links `port/base/*.cc` and BoringSSL. It is compiled with the core's
settings and run by `build/build.py` (`PORT_TESTS`).
Its last line is `base_test: N checks passed`.
`build/build_test.py` fails when a source under `port/` is linked by no
test.

What is expected is what upstream's `client/compat/base.cc` (at `2931e0e`)
does, and each test names the lines: base64 with the standard alphabet and
padding, a strict decode that leaves the output alone when it fails,
integers as plain decimals, and doubles as the shortest digits that read
back, without an exponent while the decimal point sits in (-6, 12] and as
`1.5e+21` otherwise. The text of a double has to be the same on every
platform, because what one writes another reads.

Not pinned by a test: infinities and NaN. Upstream's steps turn the text
`std::to_chars` gives for them into `i.f` and `n.n`, and what MSVC's
`std::to_chars` gives for a NaN could not be determined from the source.
No caller in the core passes a double today (`phi_tab_store.cc` writes
strings and `int`s).

### `store_files_test` and `store_usable_test` (`port/store/`)

`store_files_test.cc` covers the durable write (`WriteFileDurable`), the
account directory names and the directory tree. It does not open a store.
`store_usable_test.cc` opens the guest account's store in a temporary root,
makes a Space, a pinned tab, a bookmark folder and a bookmark in it, closes,
opens again with a new object and reads the same rows back; it also
compares `PrepareManifest` with the imported store's own manifest handling
for nine manifest states (absent, current, older, newer, unreadable), byte
for byte. Their last lines are `store_files_test: N checks passed` and
`store_usable_test: N checks passed`.

The write is tested three ways: the order of the calls (write the temp
file, fsync it, rename, fsync the directory) against a recording file
system; a failure injected at each step, which must leave the old file and
no temp file; and a writer killed with SIGKILL at random moments 40 times,
after which the file is always one whole payload or the other. The first
two argue for power loss, the third only for process death: a power cut
itself is not tested.

The expectations come from Windows `client/phi_local_store.cc:105-124`
(temp file, flush, replace with write-through) and
`client/phi_account_storage.cc`, and from the imported store's code for the
manifest states. What is not pinned: the Mac's durability (Foundation's
atomic write; whether it fsyncs is not in the Mac sources).
