// Copyright 2026 Phinomenon Inc.
// Licensed under the Apache License, Version 2.0; see the LICENSE file.

#ifndef PHI_CLIENT_CORE_PORT_STORE_PHI_STORE_FILES_H_
#define PHI_CLIENT_CORE_PORT_STORE_PHI_STORE_FILES_H_

#include <sys/types.h>

#include <string>
#include <string_view>

#include "client/phi_local_store.h"

// The Linux side of the account local store: the files around
// client/phi_local_store.h, which is imported and cannot be edited here.
//
// What is ported, and from where (phibrowser-win at 2931e0e):
//  - the durable manifest write: phi_local_store.cc:105-124
//    (WriteWholeFileAtomic: write a temp file, FlushFileBuffers, then
//    MoveFileExW with REPLACE_EXISTING | WRITE_THROUGH). The imported file's
//    non-Windows branch (:136-146) is a stand-in with no flush; it stays, but
//    PrepareManifest() writes the manifest first, so the stand-in is not
//    reached for any state it would act on.
//  - the account directory tree: client/phi_account_storage.{h,cc}
//    (PhiAccountDirName, PhiEnsureAccountDir). The ROOT is a parameter: this
//    code bakes in no location.
//
// Plain POSIX, no base/, no exceptions.

namespace phi_client::port {

// The file system calls WriteFileDurable makes, one pointer each, so a test
// can make any step fail and see what the target is left as. Same signatures
// as the C library's.
struct FileOps {
  int (*open)(const char* path, int flags, mode_t mode);
  ssize_t (*write)(int fd, const void* data, size_t size);
  int (*fsync)(int fd);
  int (*close)(int fd);
  int (*rename)(const char* from, const char* to);
  int (*unlink)(const char* path);
};
const FileOps& PosixFileOps();

// Replaces `path` with `data` so that a crash at any point leaves either the
// old file or the whole new one, and the new one is on disk when this
// returns true: write `path`.tmp, fsync it, rename it over `path`, fsync the
// directory. On false `path` is untouched, except when only the final
// directory fsync failed (the new file is in place but not known durable);
// the temp file is removed.
bool WriteFileDurable(const std::string& path,
                      std::string_view data,
                      const FileOps& ops = PosixFileOps());

// Guest Mode's account id, also every build without a signed-in account
// (Mac Account.swift:91; Windows phi_account_storage.h:30).
inline constexpr char kGuestAccountId[] = "default-account-id";

// The directory name of an account: Windows phi_account_storage.cc:17-43
// (characters a Windows file name refuses, and '/', become '_'; a trailing
// '.' or ' ' too; empty becomes the guest id). The Mac uses the raw id;
// the two agree for the guest and for every id without those characters.
std::string AccountDirName(std::string_view account_id);

// <root>/users/<AccountDirName(account_id)>, without creating anything.
std::string AccountDirPath(const std::string& root,
                           std::string_view account_id);

// Creates <root> (and its parents, as the Mac does: LocalStore.swift:117),
// <root>/users, the account directory and its localDB/ and defaults/
// subdirectories (the tree of Windows phi_account_storage.cc:82-104). The
// directories made below <root> are mode 0700; <root> and any parents made
// for it get the process default. Returns the account directory, or empty on
// failure.
std::string EnsureAccountDir(const std::string& root,
                             std::string_view account_id);

// What LocalStore::Open would do to the manifest in `dir`
// (phi_local_store.cc:539-547, 593-596), done here with WriteFileDurable:
// absent, unreadable or older than store::kStoreFormatVersion => write the
// current one; the current one or a newer one => leave it (a newer one makes
// Open() return kRequiresNewerApp). False when a needed write failed.
bool PrepareManifest(const std::string& dir,
                     const FileOps& ops = PosixFileOps());

// The one call a host makes: EnsureAccountDir, PrepareManifest on its
// localDB/, then store.Open() there. `account_dir` (optional) receives the
// account directory. kFailed when a directory or the manifest cannot be
// made; `store.last_error()` is empty in that case.
store::LocalStore::OpenResult OpenAccountStore(
    store::LocalStore& store,
    const std::string& root,
    std::string_view account_id,
    std::string* account_dir = nullptr);

}  // namespace phi_client::port

#endif  // PHI_CLIENT_CORE_PORT_STORE_PHI_STORE_FILES_H_
