#!/bin/sh
# Run a build command with an optional, temporary TLS compatibility profile.
# Mount this file read-only into the RUN step; do not change runtime TLS config.
set -eu
if [ "$#" -lt 2 ]; then
    echo 'Usage: sh build_tls.sh 0|1 command [arguments...]' >&2
    exit 2
fi
mode="$1"
shift
case "$mode" in
    0) exec "$@" ;;
    1) ;;
    *) echo 'BUILD_TLS_COMPAT must be 0 or 1.' >&2; exit 2 ;;
esac

tls_config="$(mktemp /tmp/score-it-build-tls.XXXXXX)"
trap 'rm -f "$tls_config"' EXIT
cat >"$tls_config" <<'CONF'
openssl_conf = score_it_init
[score_it_init]
ssl_conf = score_it_ssl
[score_it_ssl]
system_default = score_it_tls
[score_it_tls]
Groups = X25519:P-256:P-384
CONF
export OPENSSL_CONF="$tls_config"
export GODEBUG="${GODEBUG:+$GODEBUG,}tlsmlkem=0,tlssecpmlkem=0"
"$@"
