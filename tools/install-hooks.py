#!/usr/bin/env python3
# Copyright 2026 Phinomenon Inc.
# Licensed under the Apache License, Version 2.0; see the LICENSE file.
"""Installs the pre-commit hook that runs tools/check.py --fast.

  tools/install-hooks.py [--repo DIR]

Nothing installs the hook for you: run this once in every clone. Running it
again is safe: an up-to-date hook is left alone, an older one of ours is
updated, and a hook someone else wrote is never overwritten (the script
fails and says so).

The hook is not to be skipped: do not commit with --no-verify. A check that
is wrong is fixed in tools/check.py, in a commit of its own.
"""

import argparse
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER = "# Installed by phi-client-core tools/install-hooks.py"

# git runs a pre-commit hook in the top directory of the work tree, so each
# worktree checks itself with its own tools/check.py. A checkout without the
# script is refused, never let through unchecked.
HOOK = """\
#!/bin/sh
%s; rerun it to update.
if [ ! -f tools/check.py ]; then
  echo "pre-commit: this checkout has no tools/check.py, so the commit" \\
    "cannot be checked. Bring the checks into this branch first." >&2
  exit 1
fi
exec python3 tools/check.py --fast >&2
""" % MARKER


def fail(message):
    sys.exit("install-hooks: " + message)


def install_hook(repo):
    result = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--path-format=absolute",
         "--git-path", "hooks/pre-commit"], capture_output=True, text=True)
    if result.returncode != 0:
        fail("%s is not a git repository: %s" % (repo, result.stderr.strip()))
    path = result.stdout.strip()
    if os.path.exists(path):
        with open(path) as f:
            current = f.read()
        if current == HOOK:
            print("up to date: " + path)
            return
        if MARKER not in current:
            fail("%s is a pre-commit hook this script did not write; merge "
                 "it by hand or move it away and rerun" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(HOOK)
    os.chmod(path, 0o755)
    print("installed " + path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=REPO,
                        help="default: this repository")
    install_hook(parser.parse_args().repo)


if __name__ == "__main__":
    main()
