#!/bin/sh
# Build/host-supplied probe: rwx permissions, links, editing and archives.
set -eu
stage=user
probe_dir=
trap 'status=$?; if [ "$status" -ne 0 ]; then printf "NOACL_PROBE_FAILED stage=%s exit=%s\n" "$stage" "$status" >&2; fi; if [ -n "$probe_dir" ]; then rm -rf "$probe_dir"; fi' EXIT
test "$(id -u)" = "${1:?Expected unprivileged UID}"
test "$1" -gt 0
probe_dir="$(mktemp -d)"
umask 077
stage=copy_mode
printf 'source\n' >"$probe_dir/input"
chmod 640 "$probe_dir/input"
cp --preserve=mode,timestamps "$probe_dir/input" "$probe_dir/copied"
test "$(stat -c %a "$probe_dir/copied")" = 640
stage=links
mv "$probe_dir/copied" "$probe_dir/moved"
ln "$probe_dir/moved" "$probe_dir/hardlink"
test "$(stat -c %i "$probe_dir/moved")" = "$(stat -c %i "$probe_dir/hardlink")"
ln -s input "$probe_dir/symlink"
cp -P "$probe_dir/symlink" "$probe_dir/symlink-copy"
test -L "$probe_dir/symlink-copy"
test "$(readlink "$probe_dir/symlink-copy")" = input
stage=install_mode
install -m 600 "$probe_dir/moved" "$probe_dir/installed"
test "$(stat -c %a "$probe_dir/installed")" = 600
stage=sed_in_place
sed -i 's/source/checked/' "$probe_dir/moved"
test "$(stat -c %a "$probe_dir/moved")" = 640
test "$(cat "$probe_dir/moved")" = checked
stage=archive_roundtrip
tar -C "$probe_dir" -cf "$probe_dir/roundtrip.tar" moved installed symlink-copy
mkdir "$probe_dir/restored"
tar -C "$probe_dir/restored" -xpf "$probe_dir/roundtrip.tar"
stage=restored_content_and_modes
cmp "$probe_dir/moved" "$probe_dir/restored/moved"
cmp "$probe_dir/installed" "$probe_dir/restored/installed"
test "$(stat -c %a "$probe_dir/restored/moved")" = 640
test "$(stat -c %a "$probe_dir/restored/installed")" = 600
test -L "$probe_dir/restored/symlink-copy"
test "$(readlink "$probe_dir/restored/symlink-copy")" = input
printf 'NOACL_POSIX_TOOLS_OK uid=%s\n' "$(id -u)"
