#!/bin/sh
# Remove only the reviewed package set, using installed metadata and no downloads.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
PROFILE="${1:?Expected app or postgres}"
ALLOW_ESSENTIAL=''
case "$PROFILE" in
    app)
        . /etc/os-release
        test "$ID:$VERSION_CODENAME" = debian:trixie
        # Debian 13 util-linux links lastlog2/wtmpdb to SQLite. This app runs
        # directly as UID 10001 and uses no OS login, runuser or mount tools.
        # Remove the actual packages, retaining consistent dpkg metadata.
        # util-linux is Essential for a general Debian system; the exception
        # applies only to this fixed, disposable application image after
        # account creation. The host and PostgreSQL never use this flag.
        set -- util-linux liblastlog2-2 libsqlite3-0
        if [ "$(dpkg-query -W -f='${db:Status-Status}' libwtmpdb0 2>/dev/null || true)" = installed ]; then
            set -- "$@" libwtmpdb0
        fi
        ALLOW_ESSENTIAL=--allow-remove-essential
        ;;
    postgres) set -- gpg libsqlite3-0 ;;
    *) echo 'Unknown runtime package profile.' >&2; exit 1 ;;
esac

PLAN="$(mktemp)"
trap 'rm -f "$PLAN" "$PLAN.packages"' EXIT HUP INT TERM
apt-get --simulate ${ALLOW_ESSENTIAL:+"$ALLOW_ESSENTIAL"} -o APT::Get::AutomaticRemove=false purge "$@" >"$PLAN"
cat "$PLAN"
if grep -Eq '^(Inst|Conf) ' "$PLAN"; then
    echo 'Unexpected installation/configuration in the purge plan.' >&2
    exit 1
fi
awk '$1 == "Purg" || $1 == "Remv" { sub(/:.*/, "", $2); print $2 }' \
    "$PLAN" >"$PLAN.packages"
while IFS= read -r PACKAGE; do
    case "$PROFILE:$PACKAGE" in
        app:libsqlite3-0|app:util-linux|app:liblastlog2-2|app:libwtmpdb0|postgres:libsqlite3-0|postgres:gpg|postgres:gnupg|postgres:gpg-wks-client|postgres:gpg-wks-server) ;;
        *) echo "Unexpected package removal: $PACKAGE" >&2; exit 1 ;;
    esac
done <"$PLAN.packages"
for PACKAGE do
    grep -Fx "$PACKAGE" "$PLAN.packages" >/dev/null
done

# Do not autoremove other packages or change runtime library versions.
apt-get -y ${ALLOW_ESSENTIAL:+"$ALLOW_ESSENTIAL"} -o APT::Get::AutomaticRemove=false purge "$@"
apt-get check
test -z "$(dpkg --audit)"
# Keep the live package inventory; discard dpkg's prior-state snapshot.
rm -f /var/lib/dpkg/status-old
if dpkg-query -W -f='${db:Status-Status}\n' libsqlite3-0 2>/dev/null | grep -Fx installed; then
    echo 'SQLite package remains installed.' >&2
    exit 1
fi
if [ "$PROFILE" = app ]; then
    python - <<'PY'
import importlib.util
from pathlib import Path
import shutil
import sysconfig

shutil.rmtree(Path(sysconfig.get_path('stdlib')) / 'sqlite3')
for path in Path(sysconfig.get_config_var('DESTSHARED')).glob('_sqlite3*.so'):
    path.unlink()
assert importlib.util.find_spec('sqlite3') is None
assert importlib.util.find_spec('_sqlite3') is None
PY
fi
echo "RUNTIME_SQLITE_REMOVED=$PROFILE"
