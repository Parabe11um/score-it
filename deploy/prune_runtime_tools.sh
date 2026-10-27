#!/bin/sh
# Build-stage only: remove unused OS commands before flattening runtime layers.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
PROFILE="${1:?Expected app or postgres}"
. /etc/os-release
case "$PROFILE:$ID:$VERSION_CODENAME" in
    app:debian:trixie) set -- mount ncurses-bin libmount1 libblkid1 adduser passwd libacl1 ;;
    postgres:debian:bookworm) set -- mount ncurses-bin util-linux util-linux-extra e2fsprogs libmount1 libblkid1 libacl1 ;;
    *) echo 'Unexpected runtime tools profile or base.' >&2; exit 1 ;;
esac

# ncurses-bin (and util-linux in PG) are Essential for a general Debian system.
# These fixed container runtimes need neither terminal administration, OS login
# nor namespace/mount commands. ext filesystem administration belongs to the
# host; e2fsprogs is the remaining PG libblkid consumer after mount is removed.
# coreutils/sed/tar have been rebuilt without ACL support. The APP account is
# already created: passwd's useradd/usermod are its last libacl consumers;
# adduser is their dependent account-management wrapper, not a runtime need.
# Keep /etc/passwd and /etc/group, OpenSSL, libc/ICU and terminfo unchanged.
# The exception is scoped to this exact purge list; no host installs use it.
PLAN="$(mktemp)"
trap 'rm -f "$PLAN" "$PLAN.packages" "$PLAN.identities"' EXIT HUP INT TERM
sha256sum /etc/passwd /etc/group >"$PLAN.identities"
apt-get --simulate --allow-remove-essential -o APT::Get::AutomaticRemove=false purge "$@" >"$PLAN"
cat "$PLAN"
if grep -Eq '^(Inst|Conf) ' "$PLAN"; then
    echo 'Unexpected installation/configuration in the runtime tools purge plan.' >&2
    exit 1
fi
awk '$1 == "Purg" || $1 == "Remv" { sub(/:.*/, "", $2); print $2 }' "$PLAN" >"$PLAN.packages"
while IFS= read -r PACKAGE; do
    case "$PROFILE:$PACKAGE" in
        app:mount|app:ncurses-bin|app:libmount1|app:libblkid1|app:adduser|app:passwd|app:libacl1|\
        postgres:mount|postgres:ncurses-bin|postgres:util-linux|postgres:util-linux-extra|\
        postgres:e2fsprogs|postgres:libmount1|postgres:libblkid1|postgres:libacl1) ;;
        *) echo "Unexpected runtime tools package removal: $PACKAGE" >&2; exit 1 ;;
    esac
done <"$PLAN.packages"
for PACKAGE do
    grep -Fx "$PACKAGE" "$PLAN.packages" >/dev/null
done
apt-get -y --allow-remove-essential -o APT::Get::AutomaticRemove=false purge "$@"
sha256sum --check "$PLAN.identities"
apt-get check
test -z "$(dpkg --audit)"
rm -f /var/lib/dpkg/status-old
for PACKAGE do
    if [ "$(dpkg-query -W -f='${db:Status-Status}' "$PACKAGE" 2>/dev/null || true)" = installed ]; then
        echo "Removed runtime tools package remains installed: $PACKAGE" >&2
        exit 1
    fi
done

# Containers enter as root or their configured user; gosu drops privileges
# directly. None of these runtime entry points needs file-based elevation.
# Do this after all package operations and before copying into FROM scratch.
find / -xdev \( -path /proc -o -path /sys -o -path /dev -o -path /run \) -prune -o \
    -type f -perm /6000 -exec chmod a-s -- {} +
echo "RUNTIME_TOOLS_REMOVED=$PROFILE"
