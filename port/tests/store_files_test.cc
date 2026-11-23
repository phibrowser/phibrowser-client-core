// Copyright 2026 Phinomenon Inc.
// Licensed under the Apache License, Version 2.0; see the LICENSE file.

// Tests of port/store/phi_store_files: the durable file write and the
// account directory tree. Every expectation names its source ("Windows:" is
// phibrowser-win at 2931e0e, "Mac:" is phibrowser-mac).

#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iterator>
#include <string>
#include <string_view>
#include <vector>

#include "port/store/phi_store_files.h"

namespace {

namespace fs = std::filesystem;
using phi_client::port::FileOps;

int g_checks = 0;
int g_failures = 0;

void Check(bool ok, const char* what, int line) {
  ++g_checks;
  if (!ok) {
    ++g_failures;
    std::fprintf(stderr, "store_files_test.cc:%d: FAIL %s\n", line, what);
  }
}
#define CHECK(condition) Check((condition), #condition, __LINE__)

std::string TempDir(const char* name) {
  const fs::path dir =
      fs::temp_directory_path() /
      ("phi_store_files_test_" + std::to_string(::getpid()) + "_" + name);
  fs::remove_all(dir);
  fs::create_directories(dir);
  return dir.string();
}

std::string Slurp(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  return std::string(std::istreambuf_iterator<char>(in),
                     std::istreambuf_iterator<char>());
}

void Put(const std::string& path, const std::string& data) {
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  out << data;
}

// --- A recording and failing file system
// ---------------------------------------

std::vector<std::string> g_log;
std::string g_fail_at;  // "" or the call that fails: "open_tmp", "write", ...
bool g_short_writes = false;
bool g_eintr_once = false;
int g_fsyncs = 0;

std::string Base(const char* path) {
  return fs::path(path).filename().string();
}

int RecOpen(const char* path, int flags, mode_t mode) {
  const bool dir = (flags & O_DIRECTORY) != 0;
  g_log.push_back(std::string("open:") + (dir ? "<dir>" : Base(path)));
  if (!dir && g_fail_at == "open_tmp") {
    errno = EACCES;
    return -1;
  }
  if (dir && g_fail_at == "open_dir") {
    errno = EACCES;
    return -1;
  }
  return ::open(path, flags, mode);
}

ssize_t RecWrite(int fd, const void* data, size_t size) {
  g_log.push_back("write");
  if (g_fail_at == "write") {
    errno = ENOSPC;
    return -1;
  }
  if (g_eintr_once) {
    g_eintr_once = false;
    errno = EINTR;
    return -1;
  }
  if (g_short_writes) {
    return ::write(fd, data, size > 3 ? 3 : size);
  }
  return ::write(fd, data, size);
}

int RecFsync(int fd) {
  g_log.push_back("fsync");
  ++g_fsyncs;
  const bool is_dir = g_fsyncs > 1;  // Order is asserted in its own test.
  if ((g_fail_at == "fsync_file" && !is_dir) ||
      (g_fail_at == "fsync_dir" && is_dir)) {
    errno = EIO;
    return -1;
  }
  return ::fsync(fd);
}

int RecClose(int fd) {
  g_log.push_back("close");
  return ::close(fd);
}

int RecRename(const char* from, const char* to) {
  g_log.push_back("rename:" + Base(from) + "->" + Base(to));
  if (g_fail_at == "rename") {
    errno = EXDEV;
    return -1;
  }
  return ::rename(from, to);
}

int RecUnlink(const char* path) {
  g_log.push_back("unlink:" + Base(path));
  return ::unlink(path);
}

const FileOps kRecording{RecOpen,  RecWrite,  RecFsync,
                         RecClose, RecRename, RecUnlink};

void ResetRecording() {
  g_log.clear();
  g_fail_at.clear();
  g_short_writes = false;
  g_eintr_once = false;
  g_fsyncs = 0;
}

// --- WriteFileDurable
// ----------------------------------------------------------

// Windows: phi_local_store.cc:105-124 writes the temp file, flushes it, then
// replaces the target with write-through. The POSIX twin of the same three
// steps, plus the directory entry (the rename itself) made durable.
void TestWriteReplacesAndLeavesNoTemp() {
  const std::string dir = TempDir("write");
  const std::string path = dir + "/m.json";
  CHECK(phi_client::port::WriteFileDurable(path, "first"));
  CHECK(Slurp(path) == "first");
  CHECK(phi_client::port::WriteFileDurable(path, "second, longer"));
  CHECK(Slurp(path) == "second, longer");
  CHECK(phi_client::port::WriteFileDurable(path, ""));
  CHECK(fs::exists(path) && Slurp(path).empty());
  CHECK(!fs::exists(path + ".tmp"));
  struct stat st{};
  CHECK(::stat(path.c_str(), &st) == 0 && (st.st_mode & 0777) == 0600);
  CHECK(!phi_client::port::WriteFileDurable(dir + "/no/such/dir/m.json", "x"));
}

void TestStepsRunInTheDurableOrder() {
  const std::string dir = TempDir("order");
  const std::string path = dir + "/m.json";
  ResetRecording();
  CHECK(phi_client::port::WriteFileDurable(path, "payload", kRecording));
  const std::vector<std::string> expected = {
      "open:m.json.tmp",           "write",      "fsync", "close",
      "rename:m.json.tmp->m.json", "open:<dir>", "fsync", "close"};
  CHECK(g_log == expected);
  if (g_log != expected) {
    for (const std::string& line : g_log) {
      std::fprintf(stderr, "  got: %s\n", line.c_str());
    }
  }
  CHECK(g_fsyncs == 2);
}

void TestPartialWritesAndEintrAreFinished() {
  const std::string dir = TempDir("partial");
  const std::string path = dir + "/m.json";
  const std::string payload(1000, 'x');
  ResetRecording();
  g_short_writes = true;
  CHECK(phi_client::port::WriteFileDurable(path, payload, kRecording));
  CHECK(Slurp(path) == payload);
  ResetRecording();
  g_eintr_once = true;
  CHECK(phi_client::port::WriteFileDurable(path, "after eintr", kRecording));
  CHECK(Slurp(path) == "after eintr");
}

// Windows: :104 "a crash never leaves a truncated manifest": a failure of any
// step before the replace leaves the old file byte for byte and no temp.
void TestFailureBeforeTheRenameKeepsTheOldFile() {
  for (const char* step : {"open_tmp", "write", "fsync_file", "rename"}) {
    const std::string dir = TempDir("fail");
    const std::string path = dir + "/m.json";
    Put(path, "old contents");
    ResetRecording();
    g_fail_at = step;
    CHECK(
        !phi_client::port::WriteFileDurable(path, "new contents", kRecording));
    CHECK(Slurp(path) == "old contents");
    CHECK(!fs::exists(path + ".tmp"));
    if (Slurp(path) != "old contents" || fs::exists(path + ".tmp")) {
      std::fprintf(stderr, "  step: %s\n", step);
    }
  }
}

// Only the directory's fsync failing: the new file is in place (the rename
// happened) but not known durable, so the call says false.
void TestDirectoryFsyncFailureIsReported() {
  for (const char* step : {"open_dir", "fsync_dir"}) {
    const std::string dir = TempDir("fsyncdir");
    const std::string path = dir + "/m.json";
    Put(path, "old contents");
    ResetRecording();
    g_fail_at = step;
    CHECK(
        !phi_client::port::WriteFileDurable(path, "new contents", kRecording));
    CHECK(Slurp(path) == "new contents");
    CHECK(!fs::exists(path + ".tmp"));
  }
}

// A real SIGKILL at a random moment of a rewrite loop: the reader sees one
// whole payload or the other, never a mix or a cut. (Process death, not power
// loss: the order test above is the power-loss argument.)
void TestKilledWriterNeverLeavesATornFile() {
  const std::string dir = TempDir("kill");
  const std::string path = dir + "/m.json";
  const std::string a(size_t{256} * 1024, 'A');
  const std::string b(size_t{256} * 1024, 'B');
  CHECK(phi_client::port::WriteFileDurable(path, a));
  int torn = 0;
  for (int round = 0; round < 40; ++round) {
    const pid_t child = ::fork();
    if (child == 0) {
      for (;;) {
        phi_client::port::WriteFileDurable(path, b);
        phi_client::port::WriteFileDurable(path, a);
      }
    }
    ::usleep(static_cast<useconds_t>(1000 + (round * 937) % 9000));
    ::kill(child, SIGKILL);
    int status = 0;
    ::waitpid(child, &status, 0);
    const std::string seen = Slurp(path);
    if (seen != a && seen != b) {
      ++torn;
    }
  }
  CHECK(torn == 0);
}

// --- The account tree
// ----------------------------------------------------------

// Windows: phi_account_storage.cc:17-43. Mac: Account.swift:84-87 uses the
// raw id, which holds '|' for an Auth0 subject.
void TestAccountDirName() {
  using phi_client::port::AccountDirName;
  CHECK(AccountDirName("default-account-id") == "default-account-id");
  CHECK(AccountDirName("google-oauth2|1234") == "google-oauth2_1234");
  CHECK(AccountDirName("a/b\\c:d*e?f\"g<h>i") == "a_b_c_d_e_f_g_h_i");
  // Windows :36-38 replaces only the LAST character while it is a '.' or a
  // ' ' (the replacement is '_', which ends the loop): ported as it is.
  CHECK(AccountDirName("..") == "._");
  CHECK(AccountDirName(".") == "_");
  CHECK(AccountDirName("name. . ") == "name. ._");
  CHECK(AccountDirName(std::string_view("a\x01z", 3)) == "a_z");
  CHECK(AccountDirName("") == phi_client::port::kGuestAccountId);
  CHECK(AccountDirName("../../etc") == ".._.._etc");
}

void TestAccountDirPath() {
  CHECK(phi_client::port::AccountDirPath("/r", "default-account-id") ==
        "/r/users/default-account-id");
  CHECK(phi_client::port::AccountDirPath("/r", "x|y") == "/r/users/x_y");
}

bool IsDirWithMode(const std::string& path, mode_t mode) {
  struct stat st{};
  return ::stat(path.c_str(), &st) == 0 && S_ISDIR(st.st_mode) &&
         (st.st_mode & 0777) == mode;
}

// Windows: phi_account_storage.cc:82-104 makes Phi, users, <account>,
// localDB and defaults. Mac: LocalStore.swift:117 createDirectory with
// intermediate directories.
void TestEnsureAccountDirMakesTheTree() {
  const std::string base = TempDir("tree");
  const std::string root = base + "/deep/er/Phi";
  const std::string dir = phi_client::port::EnsureAccountDir(
      root, phi_client::port::kGuestAccountId);
  CHECK(dir == root + "/users/default-account-id");
  CHECK(IsDirWithMode(root + "/users", 0700));
  CHECK(IsDirWithMode(dir, 0700));
  CHECK(IsDirWithMode(dir + "/localDB", 0700));
  CHECK(IsDirWithMode(dir + "/defaults", 0700));
  // Idempotent, and it keeps what is there.
  Put(dir + "/localDB/keep", "k");
  CHECK(phi_client::port::EnsureAccountDir(root, "default-account-id") == dir);
  CHECK(Slurp(dir + "/localDB/keep") == "k");
  // A root below a regular file cannot be made.
  Put(base + "/file", "f");
  CHECK(phi_client::port::EnsureAccountDir(base + "/file/Phi", "x").empty());
}

}  // namespace

int main() {
  TestWriteReplacesAndLeavesNoTemp();
  TestStepsRunInTheDurableOrder();
  TestPartialWritesAndEintrAreFinished();
  TestFailureBeforeTheRenameKeepsTheOldFile();
  TestDirectoryFsyncFailureIsReported();
  TestKilledWriterNeverLeavesATornFile();
  TestAccountDirName();
  TestAccountDirPath();
  TestEnsureAccountDirMakesTheTree();
  if (g_failures != 0) {
    std::fprintf(stderr, "store_files_test: %d of %d checks failed\n",
                 g_failures, g_checks);
    return 1;
  }
  std::printf("store_files_test: %d checks passed\n", g_checks);
  return 0;
}
