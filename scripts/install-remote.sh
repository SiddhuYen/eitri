#!/bin/sh
# Install eitri with one command:
#
#     curl -fsSL https://eitri.io/install | sh
#
# What it does: installs uv if it is missing (https://docs.astral.sh/uv/, Astral's official installer),
# installs eitri as a command-line tool in its own environment with `uv tool install --python 3.12 eitri-hardware`
# (Python 3.12, the CAD kernel, the Claude Code binary from Anthropic's claude-agent-sdk package), checks that
# it works (`eitri doctor`) and prints what to run next. Nothing needs sudo; everything goes under your home folder.
#
# Settings (environment variables):
#   EITRI_NO_UV_INSTALL=1   do not install uv automatically (stop and say how instead)
#   EITRI_NO_PATH_EDIT=1    leave your shell profile alone (then run eitri by the full path printed at the end)
#   EITRI_VERSION=0.7.0     install this version instead of the newest
#   EITRI_INSTALL_SOURCE=…  install from this instead: a checkout folder, a wheel, or any uv requirement
#                           (./install.sh in a checkout sets it to the checkout)
#   EITRI_PLUGIN=1          same as --plugin
#
# Flags (after `sh -s --` when piped):
#   --plugin                (eitri 0.7.0 or newer) also register eitri as a plugin in every agent app found (Claude Code, Codex, Cursor:
#                           `eitri plugin install`) and sign in to eitri.io for the combined workspace
#                           (`eitri plugin login`; press Enter to skip):  curl -fsSL https://eitri.io/install | sh -s -- --plugin
#
# If PyPI is unreachable, it installs the release archive of the tag below from GitHub instead. An eitri installed
# under its old package name (eitri-cad, up to 0.5.6) is uninstalled first; projects, key and settings are kept.
# Measured on macOS arm64, Python 3.12, empty caches (2026-11-04): ~390 MB downloaded (Python ~20 MB + 93 wheels
# 372 MB, the largest being claude-agent-sdk 138 MB, threejs-materials 90 MB, the OCP kernel 63 MB), ~1.0 GB on disk.
#
# The whole script is one function called on the last line, so a download cut off half-way runs nothing.
set -eu

EITRI_FALLBACK_TAG="${EITRI_FALLBACK_TAG:-v0.7.0}"
EITRI_REPO="https://github.com/SiddhuYen/eitri"
PYPI_PACKAGE="eitri-hardware"
OLD_PACKAGE="eitri-cad"          # the name up to 0.5.6 (installed from GitHub); both provide the `eitri` command

say() { printf '%s\n' "$*"; }
err() { printf 'eitri install: %s\n' "$*" >&2; }

# Linux only: the CAD kernel (OpenCASCADE, via the OCP wheel) loads the system OpenGL library libGL.so.1, which
# minimal installs (servers, containers, some WSL images) lack. Measured on ubuntu:24.04: libgl1 is the one package
# needed (it brings libX11 and libexpat with it).
have_libgl() {
  for ldc in ldconfig /sbin/ldconfig /usr/sbin/ldconfig; do
    if command -v "$ldc" >/dev/null 2>&1 && "$ldc" -p 2>/dev/null | grep -q 'libGL\.so\.1'; then return 0; fi
  done
  for d in /usr/lib /usr/lib64 /lib64 /usr/lib/x86_64-linux-gnu /usr/lib/aarch64-linux-gnu /lib/x86_64-linux-gnu /lib/aarch64-linux-gnu; do
    if [ -e "$d/libGL.so.1" ]; then return 0; fi
  done
  return 1
}

libgl_hint() {
  # shellcheck disable=SC1091
  id_like="$( (. /etc/os-release 2>/dev/null && printf '%s %s' "${ID:-}" "${ID_LIKE:-}") || true)"
  case " $id_like " in
    *" debian "*|*" ubuntu "*) printf 'sudo apt-get install -y libgl1' ;;
    *" fedora "*|*" rhel "*|*" centos "*) printf 'sudo dnf install -y mesa-libGL' ;;
    *" arch "*) printf 'sudo pacman -S --needed mesa' ;;
    *" suse "*|*" opensuse "*) printf 'sudo zypper install -y Mesa-libGL1' ;;
    *) printf "install your distribution's libGL (OpenGL) package" ;;
  esac
}

eitri_install() {
  want_plugin="${EITRI_PLUGIN:-0}"
  for arg in "$@"; do
    case "$arg" in
      --plugin) want_plugin=1 ;;
      *) err "unknown option: $arg (known: --plugin)"; exit 2 ;;
    esac
  done

  case "$(uname -s)" in
    Darwin|Linux) ;;
    *) err "eitri runs on macOS and Linux; this is $(uname -s). On Windows, use WSL."; exit 1 ;;
  esac

  libgl_missing=0
  if [ "$(uname -s)" = "Linux" ] && ! have_libgl; then
    libgl_missing=1
  fi

  say "eitri installer"
  say "  Downloads about 390 MB (Python, the CAD kernel, Claude Code) and uses about 1 GB of disk."
  say "  Takes 1-5 minutes depending on your connection; the first \`eitri doctor\` afterwards takes up to a minute."
  say ""
  if [ "$libgl_missing" = "1" ]; then
    say "Note: this Linux has no libGL.so.1, which the CAD kernel needs. Install it (this script does not use sudo):"
    say "    $(libgl_hint)"
    say "  The install continues; \`eitri doctor\` will pass once the library is there."
    say ""
  fi

  # --- uv -------------------------------------------------------------------------------------------
  if ! command -v uv >/dev/null 2>&1; then
    if [ -x "$HOME/.local/bin/uv" ]; then
      PATH="$HOME/.local/bin:$PATH"
    elif [ -x "$HOME/.cargo/bin/uv" ]; then
      PATH="$HOME/.cargo/bin:$PATH"
    elif [ "${EITRI_NO_UV_INSTALL:-0}" = "1" ]; then
      err "uv is not installed. Install it (https://docs.astral.sh/uv/getting-started/installation/) and run this again."
      exit 1
    else
      command -v curl >/dev/null 2>&1 || { err "curl is needed to install uv"; exit 1; }
      say "==> installing uv (the Python installer eitri uses) from https://astral.sh/uv/install.sh"
      if [ "${EITRI_NO_PATH_EDIT:-0}" = "1" ]; then
        curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
      else
        curl -LsSf https://astral.sh/uv/install.sh | sh
      fi
      PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    fi
  fi
  export PATH
  command -v uv >/dev/null 2>&1 || { err "uv was installed but is not on PATH; open a new terminal and run this again"; exit 1; }

  # --- what to install --------------------------------------------------------------------------------
  if [ -n "${EITRI_INSTALL_SOURCE:-}" ]; then
    spec="$EITRI_INSTALL_SOURCE"
    from="$spec"
  elif curl -fsS -o /dev/null --max-time 15 "https://pypi.org/simple/$PYPI_PACKAGE/" 2>/dev/null; then
    if [ -n "${EITRI_VERSION:-}" ]; then spec="$PYPI_PACKAGE==${EITRI_VERSION#v}"; else spec="$PYPI_PACKAGE"; fi
    from="PyPI"
  else
    tag="$EITRI_FALLBACK_TAG"
    if [ -n "${EITRI_VERSION:-}" ]; then tag="v${EITRI_VERSION#v}"; fi
    # a release archive, not a git URL: works on a Mac without git (no Xcode command line tools needed)
    spec="$PYPI_PACKAGE @ $EITRI_REPO/archive/refs/tags/$tag.tar.gz"
    from="GitHub release $tag (PyPI did not answer)"
  fi

  if [ -d "$(uv tool dir 2>/dev/null)/$OLD_PACKAGE" ]; then
    say "==> removing the eitri installed under its old package name ($OLD_PACKAGE); projects, key and settings stay"
    uv tool uninstall "$OLD_PACKAGE"
  fi

  say "==> installing eitri from $from"
  start=$(date +%s)
  uv tool install --force --python 3.12 "$spec"
  say "    installed in $(( $(date +%s) - start )) s"

  bin_dir="$(uv tool dir --bin)"
  eitri="$bin_dir/eitri"
  if [ ! -x "$eitri" ]; then
    err "the install finished but $eitri is missing"
    exit 1
  fi

  # --- check ---------------------------------------------------------------------------------------------
  say "==> checking the install (eitri doctor; the first run compiles the CAD kernel's Python files)"
  if ! "$eitri" doctor; then
    if [ "$(uname -s)" = "Linux" ] && ! have_libgl; then
      err "the CAD kernel needs libGL.so.1: $(libgl_hint), then run: $eitri doctor"
    else
      err "eitri is installed but \`eitri doctor\` found a problem (the FAIL lines above); AGENTS.md has a troubleshooting table"
    fi
    exit 1
  fi

  case ":$PATH:" in
    *":$bin_dir:"*) on_path=1 ;;
    *) on_path=0 ;;
  esac
  if [ "$on_path" = "0" ]; then
    say ""
    if [ "${EITRI_NO_PATH_EDIT:-0}" = "1" ]; then
      say "Note: $bin_dir is not on your PATH; run eitri as $eitri (or: export PATH=\"$bin_dir:\$PATH\")."
    else
      uv tool update-shell >/dev/null 2>&1 || true
      say "Note: $bin_dir was added to your shell's PATH; open a new terminal (or run: export PATH=\"$bin_dir:\$PATH\")."
    fi
  fi

  if [ "$want_plugin" = "1" ]; then
    if ! "$eitri" --help 2>/dev/null | grep -Eq '^[[:space:]]+plugin[[:space:]]'; then
      # `eitri plugin` and `eitri mcp` arrived in 0.7.0; an older eitri would read `plugin` as a project folder
      err "the eitri plugin needs eitri 0.7.0 or newer; this installed $("$eitri" --version 2>/dev/null | head -n1)."
      err "eitri itself is installed and works (run: eitri). Get the plugin once 0.7.0 is out: eitri update && eitri plugin install"
      exit 1
    fi
    say ""
    say "==> registering the eitri plugin in your agent apps (eitri plugin install)"
    if ! "$eitri" plugin install; then
      err "the plugin could not be registered everywhere (the FAIL lines above); fix that and run: $eitri plugin install"
      exit 1
    fi
    if ! "$eitri" plugin status 2>/dev/null | grep -q 'eitri.io token: (none)'; then
      :                                   # already signed in
    elif [ -r /dev/tty ] && [ -t 1 ]; then
      say ""
      say "==> signing in to eitri.io for the combined workspace (eitri plugin login; Enter skips)"
      # stdout stays this terminal: login prints what happens to the model files once the token is stored
      "$eitri" plugin login </dev/tty || true
    else
      say "Sign in to eitri.io for the combined workspace later:  eitri plugin login"
    fi
    say ""
    say "Done. Restart your agent (Claude Code, Codex or Cursor) and ask it for a design, for example:"
    say "      \"design a pan-tilt camera mount with two SG90 servos, powered from USB\""
    say "      The local tools need no account; the combined workspace needs  eitri plugin login  once."
    return 0
  fi

  say ""
  say "Done. Next: run   eitri"
  say "      It opens http://127.0.0.1:8765 in your browser. The demo projects work without an API key;"
  say "      add a key on the home page (or: eitri key set) to design your own parts with eitri."
  say "      Later: eitri update (newer release), eitri setup firmware (arduino-cli, for firmware projects)."
}

eitri_install "$@"
