#!/bin/sh
# Install everything needed to build and test CorvOS on Ubuntu/Debian.
# Used by CI and the dev container, so all three environments stay identical.
set -eu
PACKAGES="make python3 binutils-aarch64-linux-gnu qemu-system-arm"

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"
$SUDO apt-get update -q
$SUDO apt-get install -y -q --no-install-recommends $PACKAGES
