#!/bin/sh
# Purge only the reviewed dependency set after installing the rebuilt server.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
set -- libxml2 libxslt1.1 libllvm19 libz3-4 \
    dirmngr gnupg-l10n gnupg-utils gpg-agent gpgconf gpgsm \
    pinentry-curses libksba8 libnpth0 libassuan0 libncursesw6
PLAN="$(mktemp)"
trap 'rm -f "$PLAN" "$PLAN.packages"' EXIT HUP INT TERM
apt-get --simulate -o APT::Get::AutomaticRemove=false purge "$@" >"$PLAN"
cat "$PLAN"
if grep -Eq '^(Inst|Conf) ' "$PLAN"; then
    echo 'Unexpected installation in the dependency removal plan.' >&2
    exit 1
fi
awk '$1 == "Purg" || $1 == "Remv" { sub(/:.*/, "", $2); print $2 }' "$PLAN" >"$PLAN.packages"
while IFS= read -r PACKAGE; do
    case "$PACKAGE" in
        libxml2|libxslt1.1|libllvm19|libz3-4|dirmngr|gnupg-l10n|gnupg-utils|gpg-agent|gpgconf|gpgsm|pinentry-curses|libksba8|libnpth0|libassuan0|libncursesw6) ;;
        *) echo "Unexpected package removal: $PACKAGE" >&2; exit 1 ;;
    esac
done <"$PLAN.packages"
for PACKAGE do
    grep -Fx "$PACKAGE" "$PLAN.packages" >/dev/null
done
apt-get -y -o APT::Get::AutomaticRemove=false purge "$@"
apt-get check
test -z "$(dpkg --audit)"
rm -f /var/lib/dpkg/status-old
echo POSTGRES_OPTIONAL_DEPENDENCIES_REMOVED
