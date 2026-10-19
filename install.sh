#!/usr/bin/env sh
# Install eitri from this checkout with one command:   ./install.sh
#
# What it does: installs uv if it is missing (https://docs.astral.sh/uv/, the official installer),
# installs eitri as a command-line tool in its own environment (Python 3.12, the CAD kernel, the
# Claude Code binary - about 900 MB installed), checks that it works (`eitri doctor`), and prints what to run next.
# Set EITRI_NO_UV_INSTALL=1 to refuse installing uv automatically, EITRI_NO_PATH_EDIT=1 to leave your
# shell profile alone (then run eitri by its full path).
set -eu

here="$(cd "$(dirname "$0")" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
  elif [ "${EITRI_NO_UV_INSTALL:-0}" = "1" ]; then
    echo "uv is not installed. Install it (https://docs.astral.sh/uv/getting-started/installation/) and run ./install.sh again." >&2
    exit 1
  else
    echo "==> installing uv (the Python installer eitri uses) from https://astral.sh/uv/install.sh"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
fi
export PATH

echo "==> installing eitri (first time: a few minutes; it downloads the CAD kernel and Claude Code)"
uv tool install --force --python 3.12 "$here"

bin_dir="$(uv tool dir --bin)"
eitri="$bin_dir/eitri"
if [ ! -x "$eitri" ]; then
  echo "install finished but $eitri is missing" >&2
  exit 1
fi

echo "==> checking the install"
"$eitri" doctor

case ":$PATH:" in
  *":$bin_dir:"*) on_path=1 ;;
  *) on_path=0 ;;
esac
if [ "$on_path" = "0" ]; then
  echo
  if [ "${EITRI_NO_PATH_EDIT:-0}" = "1" ]; then
    echo "Note: $bin_dir is not on your PATH; run eitri as $eitri (or: export PATH=\"$bin_dir:\$PATH\")."
  else
    uv tool update-shell >/dev/null 2>&1 || true
    echo "Note: $bin_dir was added to your shell's PATH; open a new terminal (or run: export PATH=\"$bin_dir:\$PATH\")."
  fi
fi

echo
echo "Done. Next: run   eitri"
echo "      It opens http://127.0.0.1:8765 in your browser. The demo projects work without an API key;"
echo "      add a key on the home page (or: eitri key set) to design your own parts with Claude."
