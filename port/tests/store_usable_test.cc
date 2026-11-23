// Copyright 2026 Phinomenon Inc.
// Licensed under the Apache License, Version 2.0; see the LICENSE file.

// The account local store is usable end to end on Linux, in a temporary
// root: open the guest account's store, make a Space, a pinned tab, a
// bookmark folder and a bookmark in it, close, open again with a new object
// and read the same data back. And PrepareManifest does what the imported
// store's own manifest handling does, byte for byte, for every state the
// manifest can be in. The store calls are the first milestone's (Spaces,
// pinned tabs, bookmark tabs and folders); none of them has an account or the
// network as an input: the store takes a directory and an Env.

#include <unistd.h>

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iterator>
#include <optional>
#include <string>
#include <vector>

#include "client/phi_local_store.h"
#include "port/store/phi_store_files.h"

namespace {

namespace fs = std::filesystem;
using phi_client::port::FileOps;
using phi_client::store::LocalStore;
using phi_client::store::Tab;

int g_checks = 0;
int g_failures = 0;

void Check(bool ok, const char* what, int line) {
  ++g_checks;
  if (!ok) {
    ++g_failures;
    std::fprintf(stderr, "store_usable_test.cc:%d: FAIL %s\n", line, what);
  }
}
#define CHECK(condition) Check((condition), #condition, __LINE__)

std::string TempDir(const char* name) {
  const fs::path dir =
      fs::temp_directory_path() /
      ("phi_store_usable_test_" + std::to_string(::getpid()) + "_" + name);
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

constexpr char kManifest[] = "/LocalStoreCompatibility.json";

// --- End to end
// ----------------------------------------------------------------

std::vector<std::string> Titles(const std::vector<const Tab*>& rows) {
  std::vector<std::string> out;
  out.reserve(rows.size());
  for (const Tab* t : rows) {
    out.push_back(t->title);
  }
  return out;
}

void TestSpacePinBookmarkAndFolderSurviveAReopen() {
  // The working directory is empty and stays empty: the root is the only
  // place the store writes to.
  const std::string cwd = TempDir("cwd");
  const fs::path saved_cwd = fs::current_path();
  fs::current_path(cwd);
  const std::string root = TempDir("e2e") + "/Phi";

  std::string account_dir;
  std::string pin_guid;
  std::string folder_guid;
  std::string bookmark_guid;
  {
    LocalStore store;
    CHECK(phi_client::port::OpenAccountStore(
              store, root, phi_client::port::kGuestAccountId, &account_dir) ==
          LocalStore::OpenResult::kOk);
    CHECK(account_dir == root + "/users/default-account-id");
    CHECK(store.EnsureDefaultSpace("Default"));
    CHECK(store.CreateSpace("work", "Default", "Work", "#112233", "icon-work"));
    LocalStore::PinCreate pin;
    pin.url = "https://example.com/pinned";
    pin.title = "Pinned";
    pin.profile_id = "Default";
    pin.space_id = "work";
    pin_guid = store.CreatePin(pin);
    CHECK(!pin_guid.empty());
    folder_guid = store.CreateFolder("Default", "work", std::nullopt,
                                     std::nullopt, "Reading");
    CHECK(!folder_guid.empty());
    LocalStore::BookmarkCreate bookmark;
    bookmark.profile_id = "Default";
    bookmark.space_id = "work";
    bookmark.parent_guid = folder_guid;
    bookmark.title = "Example";
    bookmark.url = "https://example.com/b";
    bookmark_guid = store.CreateBookmark(bookmark);
    CHECK(!bookmark_guid.empty());
  }
  CHECK(Slurp(account_dir + "/localDB" + kManifest)
            .find("\"activeStoreFormatVersion\" : 13") != std::string::npos);
  CHECK(fs::exists(account_dir + "/localDB/LocalStore.sqlite"));
  CHECK(fs::is_directory(account_dir + "/defaults"));

  {
    LocalStore store;  // A new object: nothing is carried over in memory.
    CHECK(phi_client::port::OpenAccountStore(
              store, root, phi_client::port::kGuestAccountId) ==
          LocalStore::OpenResult::kOk);
    std::vector<std::string> space_ids;
    for (const auto& space : store.Spaces()) {
      space_ids.push_back(space.space_id);
    }
    CHECK((space_ids == std::vector<std::string>{"default-space", "work"}));
    const phi_client::store::Space* work = store.FindSpace("work");
    CHECK(work && work->name == "Work" && work->color_hex == "#112233" &&
          work->icon_name == "icon-work" && work->profile_id == "Default");
    CHECK(Titles(store.ActivePins("Default", "work")) ==
          std::vector<std::string>{"Pinned"});
    const Tab* pin = store.FindTab(pin_guid);
    CHECK(pin && pin->url == "https://example.com/pinned");
    const Tab* folder = store.FindTab(folder_guid);
    CHECK(folder &&
          folder->type == phi_client::store::RowType::kBookmarkFolder &&
          folder->title == "Reading");
    CHECK(Titles(store.Children(folder_guid)) ==
          std::vector<std::string>{"Example"});
    const Tab* bookmark = store.FindTab(bookmark_guid);
    CHECK(bookmark && bookmark->url == "https://example.com/b" &&
          bookmark->parent_guid == folder_guid);
    // A delete is on disk too.
    CHECK(store.DeleteSpaceCascade("work", /*user_intent=*/true));
  }
  {
    LocalStore store;
    CHECK(phi_client::port::OpenAccountStore(
              store, root, phi_client::port::kGuestAccountId) ==
          LocalStore::OpenResult::kOk);
    CHECK(!store.FindSpace("work") && store.FindSpace("default-space"));
    // The Space's bookmark rows go with it (upstream local_store_test.cc:
    // 436-437). Whether the pin goes depends on the pin scope (the default,
    // Profile, keeps it), so it is not asserted.
    CHECK(!store.FindTab(bookmark_guid) && !store.FindTab(folder_guid));
  }
  // Another account's data is a separate directory under the same root.
  {
    LocalStore other;
    CHECK(phi_client::port::OpenAccountStore(other, root, "auth0|someone") ==
          LocalStore::OpenResult::kOk);
    CHECK(other.Spaces().empty());
    CHECK(fs::exists(root + "/users/auth0_someone/localDB/LocalStore.sqlite"));
  }
  CHECK(fs::is_empty(cwd));
  fs::current_path(saved_cwd);
}

// A root that cannot be made is a failure, not a store somewhere else.
void TestAnUnusableRootFails() {
  const std::string base = TempDir("badroot");
  Put(base + "/file", "f");
  LocalStore store;
  CHECK(phi_client::port::OpenAccountStore(store, base + "/file/Phi",
                                           phi_client::port::kGuestAccountId) ==
        LocalStore::OpenResult::kFailed);
  CHECK(!store.is_open());
}

// --- The manifest, against the imported store's own handling
// --------------------

struct Counting {
  static int fsyncs;
  static int Fsync(int fd) {
    ++fsyncs;
    return ::fsync(fd);
  }
};
int Counting::fsyncs = 0;

// For each state the manifest can be in: open one directory with the
// imported store alone and another through PrepareManifest, and compare the
// open result and the manifest's bytes. The expected values are the imported
// code's: phi_local_store.cc:539-547 (newer => refused, nothing written) and
// :593-596 (absent or not the current version => the current manifest).
void TestPrepareManifestAgreesWithTheImportedStore() {
  struct Case {
    const char* name;
    bool has_file;
    std::string text;
    bool writes;  // PrepareManifest should write.
  };
  const std::string current =
      "{\n  \"activeStoreFormatVersion\" : 13,\n  \"backups\" : [\n\n  ],\n"
      "  \"manifestVersion\" : 1\n}";
  const std::vector<Case> cases = {
      {"absent", false, "", true},
      {"current", true, current, false},
      {"current-other-bytes", true, "{\"activeStoreFormatVersion\":13}", false},
      {"older", true, "{\"activeStoreFormatVersion\" : 12}", true},
      {"newer", true, "{\"activeStoreFormatVersion\" : 14}", false},
      {"much-newer", true, "{\"activeStoreFormatVersion\" : 99}", false},
      {"empty", true, "", true},
      {"garbage", true, "not json at all", true},
      {"no-number", true, "{\"activeStoreFormatVersion\" : }", true},
  };
  const FileOps counting{phi_client::port::PosixFileOps().open,
                         phi_client::port::PosixFileOps().write,
                         Counting::Fsync,
                         phi_client::port::PosixFileOps().close,
                         phi_client::port::PosixFileOps().rename,
                         phi_client::port::PosixFileOps().unlink};
  for (const Case& c : cases) {
    const std::string stock_dir =
        TempDir((std::string("m_stock_") + c.name).c_str());
    const std::string ours_dir =
        TempDir((std::string("m_ours_") + c.name).c_str());
    for (const std::string& dir : {stock_dir, ours_dir}) {
      if (c.has_file) {
        Put(dir + kManifest, c.text);
      }
    }
    LocalStore stock;
    const LocalStore::OpenResult stock_result = stock.Open(stock_dir);
    Counting::fsyncs = 0;
    CHECK(phi_client::port::PrepareManifest(ours_dir, counting));
    const int fsyncs = Counting::fsyncs;
    LocalStore ours;
    const LocalStore::OpenResult ours_result = ours.Open(ours_dir);
    CHECK(stock_result == ours_result);
    CHECK(Slurp(stock_dir + kManifest) == Slurp(ours_dir + kManifest));
    CHECK(fsyncs == (c.writes ? 2 : 0));
    CHECK(!fs::exists(ours_dir + kManifest + ".tmp"));
    if (Slurp(stock_dir + kManifest) != Slurp(ours_dir + kManifest) ||
        fsyncs != (c.writes ? 2 : 0)) {
      std::fprintf(stderr, "  case: %s\n", c.name);
    }
  }
}

}  // namespace

int main() {
  TestSpacePinBookmarkAndFolderSurviveAReopen();
  TestAnUnusableRootFails();
  TestPrepareManifestAgreesWithTheImportedStore();
  if (g_failures != 0) {
    std::fprintf(stderr, "store_usable_test: %d of %d checks failed\n",
                 g_failures, g_checks);
    return 1;
  }
  std::printf("store_usable_test: %d checks passed\n", g_checks);
  return 0;
}
