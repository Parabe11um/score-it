#!/bin/sh
# Normal rwx permissions, links, in-place editing and archive roundtrips.
set -eu
test "$(id -u)" = "${1:?Expected unprivileged UID}"
test "$1" -gt 0
probe_dir="$(mktemp -d)"
trap 'rm -rf "$probe_dir"' EXIT
umask 077
printf 'source\n' >"$probe_dir/input"
chmod 640 "$probe_dir/input"
cp --preserve=mode,timestamps "$probe_dir/input" "$probe_dir/copied"
test "$(stat -c %a "$probe_dir/copied")" = 640
mv "$probe_dir/copied" "$probe_dir/moved"
ln "$probe_dir/moved" "$probe_dir/hardlink"
test "$(stat -c %i "$probe_dir/moved")" = "$(stat -c %i "$probe_dir/hardlink")"
ln -s input "$probe_dir/symlink"
cp -P "$probe_dir/symlink" "$probe_dir/symlink-copy"
test -L "$probe_dir/symlink-copy"
test "$(readlink "$probe_dir/symlink-copy")" = input
install -m 600 "$probe_dir/moved" "$probe_dir/installed"
test "$(stat -c %a "$probe_dir/installed")" = 600
sed -i 's/source/checked/' "$probe_dir/moved"
test "$(stat -c %a "$probe_dir/moved")" = 640
test "$(cat "$probe_dir/moved")" = checked
tar -C "$probe_dir" -cf "$probe_dir/roundtrip.tar" moved installed symlink-copy
mkdir "$probe_dir/restored"
tar -C "$probe_dir/restored" -xf "$probe_dir/roundtrip.tar"
cmp "$probe_dir/moved" "$probe_dir/restored/moved"
cmp "$probe_dir/installed" "$probe_dir/restored/installed"
test "$(stat -c %a "$probe_dir/restored/moved")" = 640
test "$(stat -c %a "$probe_dir/restored/installed")" = 600
test -L "$probe_dir/restored/symlink-copy"
test "$(readlink "$probe_dir/restored/symlink-copy")" = input
printf 'NOACL_POSIX_TOOLS_OK uid=%s\n' "$(id -u)"
