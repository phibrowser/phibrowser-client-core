// Copyright 2026 Phinomenon Inc.
// Licensed under the Apache License, Version 2.0; see the LICENSE file.

#include "port/store/phi_store_files.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <cstddef>
#include <cstdio>
#include <fstream>
#include <ios>
#include <iterator>
#include <string>
#include <string_view>

#include "client/phi_local_store.h"
namespace phi_client::port {

namespace {

constexpr mode_t kDirMode = 0700;
constexpr mode_t kFileMode = 0600;

// The manifest, as the imported store writes it (phi_local_store.cc:177-181,
// the Mac's sorted-key pretty print).
std::string ManifestJson(int version) {
  return "{\n  \"activeStoreFormatVersion\" : " + std::to_string(version) +
         ",\n  \"backups\" : [\n\n  ],\n  \"manifestVersion\" : 1\n}";
}

// phi_local_store.cc:149-175: the number after "activeStoreFormatVersion",
// -1 when absent or unreadable.
int ManifestFormatVersion(const std::string& text) {
  const std::string key = "\"activeStoreFormatVersion\"";
  size_t at = text.find(key);
  if (at == std::string::npos) {
    return -1;
  }
  at = text.find(':', at + key.size());
  if (at == std::string::npos) {
    return -1;
  }
  ++at;
  while (at < text.size() && (text[at] == ' ' || text[at] == '\n' ||
                              text[at] == '\r' || text[at] == '\t')) {
    ++at;
  }
  int value = 0;
  bool any = false;
  while (at < text.size() && text[at] >= '0' && text[at] <= '9') {
    value = value * 10 + (text[at] - '0');
    any = true;
    ++at;
  }
  return any ? value : -1;
}

bool WriteAll(const FileOps& ops, int fd, std::string_view data) {
  while (!data.empty()) {
    const ssize_t wrote = ops.write(fd, data.data(), data.size());
    if (wrote < 0) {
      if (errno == EINTR) {
        continue;
      }
      return false;
    }
    data.remove_prefix(static_cast<size_t>(wrote));
  }
  return true;
}

bool FsyncDirectory(const FileOps& ops, const std::string& dir) {
  const int fd = ops.open(dir.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC, 0);
  if (fd < 0) {
    return false;
  }
  const bool synced = ops.fsync(fd) == 0;
  ops.close(fd);
  return synced;
}

bool IsDirectory(const std::string& path) {
  const int fd = ::open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (fd < 0) {
    return false;
  }
  ::close(fd);
  return true;
}

// mkdir -p, with the process's default mode.
bool MakeDirectory(const std::string& path) {
  for (std::string::size_type at = path.find('/', 1); at != std::string::npos;
       at = path.find('/', at + 1)) {
    ::mkdir(path.substr(0, at).c_str(), 0777);
  }
  ::mkdir(path.c_str(), 0777);
  return IsDirectory(path);
}

// One level, mode 0700 (the process umask can only narrow it).
bool MakeOwnerOnlyDirectory(const std::string& path) {
  return ::mkdir(path.c_str(), kDirMode) == 0 ||
         (errno == EEXIST && IsDirectory(path));
}

}  // namespace

const FileOps& PosixFileOps() {
  // open() is variadic in the C library, so it cannot be named directly.
  static const FileOps kOps{[](const char* path, int flags, mode_t mode) {
                              return ::open(path, flags, mode);
                            },
                            ::write,
                            ::fsync,
                            ::close,
                            ::rename,
                            ::unlink};
  return kOps;
}

bool WriteFileDurable(const std::string& path,
                      std::string_view data,
                      const FileOps& ops) {
  const std::string temp = path + ".tmp";
  const int fd = ops.open(temp.c_str(),
                          O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, kFileMode);
  if (fd < 0) {
    return false;
  }
  const bool written = WriteAll(ops, fd, data) && ops.fsync(fd) == 0;
  const bool closed = ops.close(fd) == 0;
  if (!written || !closed || ops.rename(temp.c_str(), path.c_str()) != 0) {
    ops.unlink(temp.c_str());
    return false;
  }
  const std::string::size_type slash = path.rfind('/');
  const std::string dir = slash == std::string::npos ? "."
                          : slash == 0               ? "/"
                                                     : path.substr(0, slash);
  return FsyncDirectory(ops, dir);
}

std::string AccountDirName(std::string_view account_id) {
  std::string out(account_id);
  for (char& c : out) {
    switch (c) {
      case '/':
      case ':':
      case '|':
      case '\\':
      case '*':
      case '?':
      case '"':
      case '<':
      case '>':
        c = '_';
        break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          c = '_';
        }
        break;
    }
  }
  while (!out.empty() && (out.back() == '.' || out.back() == ' ')) {
    out.back() = '_';
  }
  return out.empty() ? std::string(kGuestAccountId) : out;
}

std::string AccountDirPath(const std::string& root,
                           std::string_view account_id) {
  return root + "/users/" + AccountDirName(account_id);
}

std::string EnsureAccountDir(const std::string& root,
                             std::string_view account_id) {
  const std::string users = root + "/users";
  std::string dir = AccountDirPath(root, account_id);
  if (root.empty() || !MakeDirectory(root) || !MakeOwnerOnlyDirectory(users) ||
      !MakeOwnerOnlyDirectory(dir) ||
      !MakeOwnerOnlyDirectory(dir + "/localDB") ||
      !MakeOwnerOnlyDirectory(dir + "/defaults")) {
    return std::string();
  }
  return dir;
}

bool PrepareManifest(const std::string& dir, const FileOps& ops) {
  const std::string path = dir + "/" + store::kCompatibilityManifest;
  std::string text;
  bool has_manifest = false;
  {
    std::ifstream in(path, std::ios::binary);
    if (in) {
      has_manifest = true;
      text.assign(std::istreambuf_iterator<char>(in),
                  std::istreambuf_iterator<char>());
    }
  }
  if (has_manifest) {
    const int version = ManifestFormatVersion(text);
    if (version >= store::kStoreFormatVersion) {
      return true;
    }
  }
  return WriteFileDurable(path, ManifestJson(store::kStoreFormatVersion), ops);
}

store::LocalStore::OpenResult OpenAccountStore(store::LocalStore& store,
                                               const std::string& root,
                                               std::string_view account_id,
                                               std::string* account_dir) {
  const std::string dir = EnsureAccountDir(root, account_id);
  if (dir.empty()) {
    return store::LocalStore::OpenResult::kFailed;
  }
  if (account_dir) {
    *account_dir = dir;
  }
  const std::string db_dir = dir + "/localDB";
  if (!PrepareManifest(db_dir)) {
    return store::LocalStore::OpenResult::kFailed;
  }
  return store.Open(db_dir);
}

}  // namespace phi_client::port
