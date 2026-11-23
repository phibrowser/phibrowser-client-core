#!/usr/bin/env python3
# Copyright 2026 Phinomenon Inc.
# Licensed under the Apache License, Version 2.0; see the LICENSE file.
"""Unit tests for resync.py. Run: python3 tools/resync_test.py"""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest

import resync


def run_git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True)


class ResyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.up = os.path.join(self.tmp.name, "up")
        self.repo = os.path.join(self.tmp.name, "core")
        os.makedirs(self.up)
        os.makedirs(self.repo)
        run_git(self.up, "init", "-q", "-b", "main")
        run_git(self.up, "config", "user.name", "t")
        run_git(self.up, "config", "user.email", "t@example.invalid")
        self.first = self.commit({"client/a.cc": b"int a;\r\n",
                                  "client/b.h": b"// b\n",
                                  "third_party/lib/x.c": b"x\n"})
        manifest = {"managed_dirs": ["core", "third_party"], "groups": [{
            "name": "core", "description": "d", "origin": "o",
            "commit": self.first, "commit_date": "",
            "trees": [{"upstream": "third_party/lib/",
                       "local": "third_party/lib/", "role": "lib"}],
            "files": [
                {"upstream": "client/a.cc", "local": "core/client/a.cc",
                 "role": "a"},
                {"upstream": "client/b.h", "local": "core/client/b.h",
                 "role": "b"}]}]}
        resync.save_manifest(self.repo, manifest)

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, files, delete=()):
        for path, data in files.items():
            full = os.path.join(self.up, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(data)
        for path in delete:
            os.remove(os.path.join(self.up, path))
        run_git(self.up, "add", "-A")
        run_git(self.up, "commit", "-q", "-m", "c")
        return subprocess.run(["git", "-C", self.up, "rev-parse", "HEAD"],
                              check=True, capture_output=True,
                              text=True).stdout.strip()

    def run_tool(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = resync.main(["--repo", self.repo, *args])
        return code, out.getvalue()

    def sync(self, *args):
        return self.run_tool("sync", "--upstream", self.up, *args)

    def read(self, path):
        return resync.local_bytes(self.repo, path)

    def test_first_apply_copies_bytes_exactly_and_records_them(self):
        code, _ = self.sync("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(self.read("core/client/a.cc"), b"int a;\r\n")
        self.assertEqual(self.read("third_party/lib/x.c"), b"x\n")
        group = resync.load_manifest(self.repo)["groups"][0]
        self.assertEqual(group["commit"], self.first)
        by_local = {e["local"]: e for e in group["files"]}
        self.assertEqual(by_local["core/client/a.cc"]["sha256"],
                         resync.sha256(b"int a;\r\n"))
        self.assertEqual(by_local["third_party/lib/x.c"]["role"], "lib")
        self.assertEqual(self.run_tool("verify")[0], 0)
        self.assertEqual(self.run_tool("verify", "--upstream", self.up)[0], 0)

    def test_dry_run_reports_and_touches_nothing(self):
        self.sync("--apply")
        before = self.read("SOURCE.json")
        self.commit({"client/a.cc": b"int a2;\n",
                     "third_party/lib/new.c": b"n\n",
                     "client/unrelated.cc": b"u\n"},
                    delete=["client/b.h"])
        code, out = self.sync()
        self.assertEqual(code, 0)
        self.assertIn("changed   client/a.cc", out)
        self.assertIn("deleted   client/b.h", out)
        self.assertIn("added     third_party/lib/new.c", out)
        self.assertNotIn("unrelated", out)
        self.assertIn("dry run", out)
        self.assertEqual(self.read("SOURCE.json"), before)
        self.assertEqual(self.read("core/client/a.cc"), b"int a;\r\n")

    def test_apply_refreshes_changed_added_and_deleted(self):
        self.sync("--apply")
        second = self.commit({"client/a.cc": b"int a2;\n",
                              "third_party/lib/new.c": b"n\n"},
                             delete=["client/b.h"])
        self.assertEqual(self.sync("--apply")[0], 0)
        self.assertEqual(self.read("core/client/a.cc"), b"int a2;\n")
        self.assertEqual(self.read("third_party/lib/new.c"), b"n\n")
        self.assertIsNone(self.read("core/client/b.h"))
        group = resync.load_manifest(self.repo)["groups"][0]
        self.assertEqual(group["commit"], second)
        self.assertEqual(len(group["files"]), 3)
        self.assertEqual(self.run_tool("verify", "--upstream", self.up)[0], 0)

    def test_explicit_commit_is_honoured(self):
        self.commit({"client/a.cc": b"int a2;\n"})
        self.assertEqual(self.sync("--commit", self.first, "--apply")[0], 0)
        self.assertEqual(self.read("core/client/a.cc"), b"int a;\r\n")

    def test_apply_refuses_to_overwrite_a_local_edit(self):
        self.sync("--apply")
        with open(os.path.join(self.repo, "core/client/a.cc"), "wb") as f:
            f.write(b"edited\n")
        self.commit({"client/a.cc": b"int a2;\n"})
        code, out = self.sync("--apply")
        self.assertEqual(code, 1)
        self.assertIn("core/client/a.cc: modified locally", out)
        self.assertEqual(self.read("core/client/a.cc"), b"edited\n")
        group = resync.load_manifest(self.repo)["groups"][0]
        self.assertEqual(group["commit"], self.first)

    def test_verify_reports_edit_missing_unlisted_and_mode(self):
        self.sync("--apply")
        with open(os.path.join(self.repo, "core/client/a.cc"), "ab") as f:
            f.write(b" ")
        os.remove(os.path.join(self.repo, "core/client/b.h"))
        with open(os.path.join(self.repo, "core/client/extra.cc"), "wb"):
            pass
        os.chmod(os.path.join(self.repo, "third_party/lib/x.c"), 0o755)
        code, out = self.run_tool("verify")
        self.assertEqual(code, 1)
        self.assertIn("core/client/a.cc: checksum differs", out)
        self.assertIn("core/client/b.h: missing", out)
        self.assertIn("core/client/extra.cc: not in SOURCE.json", out)
        self.assertIn("third_party/lib/x.c: mode differs", out)

    def test_verify_against_upstream_catches_a_forged_manifest(self):
        self.sync("--apply")
        with open(os.path.join(self.repo, "core/client/a.cc"), "wb") as f:
            f.write(b"forged\n")
        manifest = resync.load_manifest(self.repo)
        for entry in manifest["groups"][0]["files"]:
            if entry["local"] == "core/client/a.cc":
                entry["sha256"] = resync.sha256(b"forged\n")
        resync.save_manifest(self.repo, manifest)
        resync.write_source_md(self.repo, manifest)
        self.assertEqual(self.run_tool("verify")[0], 0)
        code, out = self.run_tool("verify", "--upstream", self.up)
        self.assertEqual(code, 1)
        self.assertIn("client/a.cc: upstream blob at", out)

    def test_pinned_group_keeps_its_commit_and_needs_naming(self):
        manifest = resync.load_manifest(self.repo)
        manifest["groups"][0]["pinned"] = True
        resync.save_manifest(self.repo, manifest)
        self.commit({"client/a.cc": b"int a2;\n"})
        code, out = self.sync("--apply")
        self.assertEqual(code, 1)
        self.assertIn("no group to sync", out)
        self.assertEqual(self.sync("--group", "core", "--apply")[0], 0)
        self.assertEqual(self.read("core/client/a.cc"), b"int a;\r\n")

    def test_render_keeps_prose_and_verify_flags_a_stale_block(self):
        self.sync("--apply")
        path = os.path.join(self.repo, "SOURCE.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("| `core/client/a.cc` | `client/a.cc` | 1 |", text)
        self.assertIn("- Edited here: no", text)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Title\n\n" + text + "\nfooter\n")
        self.assertEqual(self.run_tool("render")[0], 0)
        with open(path, encoding="utf-8") as f:
            again = f.read()
        self.assertTrue(again.startswith("# Title\n\n"))
        self.assertTrue(again.endswith("\nfooter\n"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(again.replace("| 1 |", "| 9 |"))
        code, out = self.run_tool("verify")
        self.assertEqual(code, 1)
        self.assertIn("SOURCE.md: stale", out)

    def own(self):
        """Turns the group into an owned one, as the real manifest records
        it: no checksum, mode, blob or line count."""
        manifest = resync.load_manifest(self.repo)
        group = manifest["groups"][0]
        group["owned"] = True
        group["files"] = [
            {key: entry[key] for key in ("upstream", "local", "role")}
            for entry in group["files"]]
        resync.save_manifest(self.repo, manifest)
        resync.write_source_md(self.repo, manifest)

    def test_an_owned_file_may_be_edited_but_not_missing_or_unlisted(self):
        self.sync("--apply")
        self.own()
        with open(os.path.join(self.repo, "core/client/a.cc"), "wb") as f:
            f.write(b"edited here\n")
        os.chmod(os.path.join(self.repo, "third_party/lib/x.c"), 0o755)
        self.assertEqual(self.run_tool("verify")[0], 0)
        os.remove(os.path.join(self.repo, "core/client/b.h"))
        with open(os.path.join(self.repo, "core/client/extra.cc"), "wb"):
            pass
        code, out = self.run_tool("verify")
        self.assertEqual(code, 1)
        self.assertNotIn("core/client/a.cc", out)
        self.assertIn("core/client/b.h: missing", out)
        self.assertIn("core/client/extra.cc: not in SOURCE.json", out)

    def test_an_owned_group_is_not_compared_with_upstream(self):
        self.sync("--apply")
        self.own()
        with open(os.path.join(self.repo, "core/client/a.cc"), "wb") as f:
            f.write(b"edited here\n")
        self.commit({}, delete=["client/a.cc"])
        self.assertEqual(self.run_tool("verify", "--upstream", self.up)[0], 0)

    def test_sync_leaves_an_owned_group_alone_and_refuses_it_by_name(self):
        self.sync("--apply")
        self.own()
        with open(os.path.join(self.repo, "core/client/a.cc"), "wb") as f:
            f.write(b"edited here\n")
        before = self.read("SOURCE.json")
        self.commit({"client/a.cc": b"int a2;\n"})
        code, out = self.sync("--apply")
        self.assertEqual(code, 1)
        self.assertIn("no group to sync", out)
        code, out = self.sync("--group", "core", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("group core is edited here", out)
        self.assertEqual(self.read("core/client/a.cc"), b"edited here\n")
        self.assertEqual(self.read("SOURCE.json"), before)

    def test_an_owned_group_renders_without_lines_and_checksums(self):
        self.sync("--apply")
        checksum = resync.sha256(b"int a;\r\n")
        with open(os.path.join(self.repo, "SOURCE.md"),
                  encoding="utf-8") as f:
            self.assertIn(checksum, f.read())
        self.own()
        with open(os.path.join(self.repo, "SOURCE.md"),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertIn("- Edited here: yes", text)
        self.assertIn("| `core/client/a.cc` | `client/a.cc` | a |", text)
        self.assertNotIn(checksum, text)
        self.assertEqual(self.run_tool("verify")[0], 0)

    def test_a_file_created_in_an_owned_group_needs_only_its_entry(self):
        self.sync("--apply")
        self.own()
        with open(os.path.join(self.repo, "core/client/new.cc"), "wb") as f:
            f.write(b"int n;\n")
        self.assertEqual(self.run_tool("verify")[0], 1)
        manifest = resync.load_manifest(self.repo)
        manifest["groups"][0]["files"].append(
            {"local": "core/client/new.cc", "role": "new"})
        resync.save_manifest(self.repo, manifest)
        code, out = self.run_tool("verify")
        self.assertEqual(code, 1)
        self.assertIn("SOURCE.md: stale", out)
        self.assertEqual(self.run_tool("render")[0], 0)
        self.assertEqual(self.run_tool("verify")[0], 0)
        with open(os.path.join(self.repo, "SOURCE.md"),
                  encoding="utf-8") as f:
            self.assertIn(
                "| `core/client/new.cc` | none: created here | new |",
                f.read())

    def test_executable_mode_is_carried(self):
        full = os.path.join(self.up, "client/b.h")
        os.chmod(full, 0o755)
        self.commit({})
        self.sync("--apply")
        self.assertTrue(os.access(os.path.join(self.repo, "core/client/b.h"),
                                  os.X_OK))
        self.assertEqual(self.run_tool("verify")[0], 0)


if __name__ == "__main__":
    unittest.main()
