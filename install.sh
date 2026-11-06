#!/usr/bin/env sh
# Install eitri from this checkout with one command:   ./install.sh
#
# The same installer as `curl -fsSL https://eitri.io/install | sh` (scripts/install-remote.sh), pointed at this
# folder instead of PyPI: installs uv if it is missing, installs eitri as a command-line tool in its own environment
# (Python 3.12, the CAD kernel, the Claude Code binary - about 1 GB installed), checks that it works (`eitri doctor`),
# and prints what to run next.
# Set EITRI_NO_UV_INSTALL=1 to refuse installing uv automatically, EITRI_NO_PATH_EDIT=1 to leave your
# shell profile alone (then run eitri by its full path).
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
EITRI_INSTALL_SOURCE="$here" exec sh "$here/scripts/install-remote.sh" "$@"
