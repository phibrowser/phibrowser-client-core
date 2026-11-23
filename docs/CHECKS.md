# Checks for the code written here

`tools/check.py` is the one entry point. It checks the files written in this
repository and only those: a file that came from the Windows client
(everything `SOURCE.json` lists, and everything under `core/` and
`third_party/`) is never formatted, linted or analysed. That rule dates from
when those files were copies that were never edited here. `core/` is edited
here now (`README.md`, "Who edits what") and still keeps the style it
arrived in; whether new code there should meet these checks is not settled.
What it does check about those files is that each listed one exists, that
the vendored ones (`third_party/`) are still byte-identical and that nothing
unlisted sits beside them.

```sh
python3 tools/install-hooks.py     # once per clone: the pre-commit hook
python3 tools/check.py --fast      # what the hook runs; no build of the core
python3 tools/check.py --full --framework-include DIR   # before review
python3 tools/check.py --tidy      # clang-tidy alone
```

`DIR` (or `PHI_FRAMEWORK_INCLUDE`) and `--without-framework` are
`build/build.py`'s; see `BUILD.md`. Without either, `--full` stops before
doing anything.

Every check is on from the first commit of new code. A finding is fixed, or
the rule is changed in its configuration file with the reason written
beside it; nothing is skipped and there is no allowlist.

## What runs

Every check runs whatever the others did. A passing check prints one line
with what it looked at; a failing one prints its findings, then a line that
names it and says how to fix them. The exit status is 1 when any failed.

| Check | Mode | Looks at | Fails when |
|---|---|---|---|
| source | fast | the files `SOURCE.json` lists | one is missing, a vendored one differs from its recorded checksum, or an unlisted file sits in a managed directory (`tools/resync.py verify`) |
| copyright | fast | `.cc`, `.h`, `.py`, `.sh` written here | the first five lines lack `Copyright 2026 Phinomenon Inc.` or the Apache-2.0 line |
| format | fast | `.cc`, `.h` written here | clang-format (check only, `.clang-format`: Chromium style) would change anything |
| ascii | fast | every file written here | a character is not ASCII, or a byte is not UTF-8 |
| pyflakes | fast | `.py` written here | pyflakes reports anything (unused import, undefined name, ...) |
| pytests | fast | `*_test.py` written here | a test fails, or a test file runs no test |
| tidy | full | `.cc` written here and the headers under `port/` they include | clang-tidy reports anything (`.clang-tidy`; every finding is an error), or a file does not compile |
| build *compiler* *standard* | full | `build/build.py --repeat 3`, once each for clang++ and g++ as C++20 and C++23 | any step of the build fails (`BUILD.md`), or it reports no test binary |

A check that finds nothing to look at fails ("found nothing to check"): the
files exist, so an empty list means the check is broken.

"Written here" means: tracked by git, not listed in `SOURCE.json`, not under
one of its managed directories. The checks read the working tree of those
files, not the index: a new file is seen once it is added (`git add`), and
a commit that leaves some changes unstaged is checked with them.

Time on the build machine (128 threads, 2026-11-21): `--fast` 3.3 s, nearly
all of it the script tests; `--full` 16 s with warm build directories and
172 s from nothing (four builds of BoringSSL).

## The pre-commit hook

`python3 tools/install-hooks.py` writes `.git/hooks/pre-commit`, which runs
`tools/check.py --fast` and refuses the commit when it fails. Nothing
installs it for you: run the script once in every clone. Running it again is
safe; it never overwrites a hook it did not write.

Do not commit with `--no-verify`. A check that is wrong is fixed in
`tools/check.py` or its configuration, in a commit of its own.

The hook does not run clang-tidy on `port/` or build the core: run `--full`
before review. It does need clang-tidy and clang++ all the same, because
the script tests it runs (`tools/check_test.py`, `build/build_test.py`)
drive the real tools on small temporary files.

## Tools

| Tool | Found as | Here (2026-11) |
|---|---|---|
| clang-format | `git config phi.clang-format PATH` in this checkout, else `clang-format` on `PATH` | the framework's, `src/buildtools/linux64-format/clang-format` (23.0.0git) |
| clang-tidy | `git config phi.clang-tidy PATH`, else `clang-tidy-18` on `PATH` | the system package `clang-tidy-18` (18.1.3) |
| pyflakes | a module of the Python that runs `check.py` | 4.1.0, `python3 -m pip install pyflakes` |
| compilers | `build/build.py` | clang++ 18.1.3, g++ 13.3.0 |

Use the framework's clang-format: it is the one the framework and the Linux
client are checked with, and another version may format differently. The
`clang-format` that depot_tools puts on `PATH` only works inside a Chromium
checkout, so name the binary with `git config`.

A tool that is missing or cannot run fails its check and says how to get
it. No check is skipped.

## Rules shared with the Linux client

`.clang-format` and `.clang-tidy` are the Linux client's, copied so that a clone of this repository can run its
checks alone. Keep them the same as the client's. The differences:

- `.clang-tidy`, `misc-include-cleaner.IgnoreHeaders`: `third_party.*` here;
  the client also lists googletest and its sysroot, which this repository
  does not use.
- The comments at the top of both files.

The entry point differs in name (`tools/check.py --fast` / `--full` here,
`build-client.py --check-fast` / `--check` there) and in what exists to
check: no page, no bundle, no framework lock. This repository adds the
copyright check, pyflakes, the "found nothing to check" rule and the
`source` check.

## What the checks do not cover

- Whether the code is right: that is the tests' job, and `BUILD.md` and
  `TESTS.md` say what the tests reach. Only `port/tests/` exists; the sync
  engine proper and most of the core are compiled but run by no test.
- The style of the files that came from the Windows client, and the
  copyright header of a file added under `core/`.
- Python formatting and typing (no formatter, no type checker).
- Markdown, JSON and the other text files beyond being ASCII.
- The content of a copyright header beyond the two lines, and files without
  a comment syntax the check knows (`.md`, `.json`, `link-closure.expected`).
- The index: the checks read the working tree of the tracked files, so a
  commit can differ from what was checked when the tree has changes that
  are not staged. Untracked files are not looked at.
- Commit messages.
- Any architecture but x86_64, any standard library but libstdc++.
- Whether a test that passed checked anything: see "A pass needs its last
  line" in `TESTS.md` for how far that is covered.
