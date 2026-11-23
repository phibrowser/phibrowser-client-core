#!/usr/bin/env python3
# Copyright 2026 Phinomenon Inc.
# Licensed under the Apache License, Version 2.0; see the LICENSE file.
"""Unit tests for install-hooks.py. Run: python3 tools/install_hooks_test.py

Each test installs into a temporary git repository whose tools/check.py is a
stand-in: its --fast fails when a tracked file contains BAD.
"""

import os
import subprocess
import sys
import tempfile
import unittest

INSTALL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "install-hooks.py")

FAKE_CHECK = """\
import subprocess, sys
assert sys.argv[1:] == ["--fast"], sys.argv
files = subprocess.run(["git", "ls-files"], capture_output=True,
                       text=True).stdout.split()
bad = [f for f in files if "BAD" in open(f).read()]
if bad:
    sys.exit("check: fast FAILED: " + ", ".join(bad))
"""


class InstallHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name
        self.git("init", "-q")
        self.write("tools/check.py", FAKE_CHECK)
        self.hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")

    def git(self, *args, check=True):
        return subprocess.run(
            ["git", "-C", self.repo, "-c", "user.name=test",
             "-c", "user.email=test@example.invalid", *args],
            check=check, capture_output=True, text=True)

    def write(self, name, text):
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def install(self):
        return subprocess.run([sys.executable, INSTALL, "--repo", self.repo],
                              capture_output=True, text=True)

    def read_hook(self):
        with open(self.hook) as f:
            return f.read()

    def commit(self, name, text):
        self.write(name, text)
        self.git("add", name)
        return self.git("commit", "-q", "-m", "test", check=False)

    def test_the_hook_blocks_a_commit_the_fast_check_fails(self):
        self.assertEqual(self.install().returncode, 0)
        result = self.commit("a.txt", "BAD\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("check: fast FAILED: a.txt", result.stderr)
        self.assertNotEqual(
            self.git("rev-parse", "-q", "--verify", "HEAD",
                     check=False).returncode, 0)

    def test_the_hook_lets_a_clean_commit_through(self):
        self.install()
        self.assertTrue(os.access(self.hook, os.X_OK))
        self.assertEqual(self.commit("a.txt", "good\n").returncode, 0)

    def test_nothing_is_installed_until_the_script_is_run(self):
        # A control for the tests above: without the hook the same commit
        # goes through, so it is the hook that blocks it.
        self.assertFalse(os.path.exists(self.hook))
        self.assertEqual(self.commit("a.txt", "BAD\n").returncode, 0)

    def test_a_checkout_without_the_check_is_refused_saying_why(self):
        self.install()
        os.remove(os.path.join(self.repo, "tools", "check.py"))
        result = self.commit("a.txt", "good\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tools/check.py", result.stderr)
        self.assertNotIn("no-verify", result.stderr)

    def test_installing_twice_gives_the_same_hook_and_writes_once(self):
        first = self.install()
        hook, mtime = self.read_hook(), os.stat(self.hook).st_mtime_ns
        second = self.install()
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        self.assertIn("installed " + self.hook, first.stdout)
        self.assertIn("up to date", second.stdout)
        self.assertEqual(self.read_hook(), hook)
        self.assertEqual(os.stat(self.hook).st_mtime_ns, mtime)
        self.assertTrue(os.access(self.hook, os.X_OK))

    def test_an_older_hook_of_ours_is_updated(self):
        self.install()
        current = self.read_hook()
        with open(self.hook, "w") as f:
            f.write(current.replace("exec ", "exec nice "))
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.read_hook(), current)

    def test_someone_elses_hook_is_left_alone(self):
        os.makedirs(os.path.dirname(self.hook), exist_ok=True)
        with open(self.hook, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(self.hook, result.stderr)
        self.assertEqual(self.read_hook(), "#!/bin/sh\nexit 0\n")

    def test_a_directory_that_is_not_a_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as plain:
            result = subprocess.run(
                [sys.executable, INSTALL, "--repo", plain],
                capture_output=True, text=True,
                env=dict(os.environ, GIT_CEILING_DIRECTORIES=plain))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a git repository", result.stderr)


if __name__ == "__main__":
    unittest.main()
