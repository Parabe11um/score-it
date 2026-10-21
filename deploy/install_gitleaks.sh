#!/bin/sh
# Install one checksummed audit executable on the connected build host only.
set -eu
DEST="${1:?Usage: sh deploy/install_gitleaks.sh /absolute/temporary-directory}"
test "$(uname -s)" = Linux
test "$(uname -m)" = x86_64
mkdir -p "$DEST"
curl --fail --silent --show-error --location --retry 3 --connect-timeout 10 --max-time 120 \
    https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
    --output "$DEST/gitleaks.tar.gz"
printf '%s  %s\n' 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb \
    "$DEST/gitleaks.tar.gz" | sha256sum --check
tar --no-same-owner -xzf "$DEST/gitleaks.tar.gz" -C "$DEST" gitleaks
test "$("$DEST/gitleaks" version)" = 8.30.1
rm "$DEST/gitleaks.tar.gz"
