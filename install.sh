#!/bin/sh
# img2threejs installer for SKILL.md hosts (Claude Code, Codex CLI, OpenCode, and compatible agents).
#
#   curl -fsSL https://raw.githubusercontent.com/img2threejs/img2threejs/main/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/img2threejs/img2threejs/main/install.sh | sh -s -- --uninstall
#
# Claude Code users can install as a plugin instead, which needs no script:
#   /plugin marketplace add img2threejs/img2threejs
#   /plugin install img2threejs@img2threejs

set -eu

NAME="img2threejs"
REPO_URL="${IMG2THREEJS_REPO:-https://github.com/img2threejs/img2threejs.git}"
REF="${IMG2THREEJS_REF:-main}"

TARGETS=""
MODE="install"

usage() {
    cat <<'EOF'
Usage: install.sh [--target DIR]... [--uninstall] [--help]

  --target DIR   Install into DIR/img2threejs instead of the auto-detected
                 skills directories. Repeatable.
  --uninstall    Remove previously installed copies.
  --help         Show this message.

Environment:
  IMG2THREEJS_REPO   Clone URL (default: the public GitHub repository)
  IMG2THREEJS_REF    Branch or tag to install (default: main)

Auto-detected skills directories:
  ~/.claude/skills          Claude Code (also read by OpenCode)
  ~/.codex/skills           Codex CLI
  ~/.config/opencode/skills OpenCode, when ~/.claude/skills is not used
  ~/.agents/skills          Generic agent skills directory
EOF
}

log() {
    printf '%s\n' "$*"
}

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

# Space-separated list membership test; skills directories never contain spaces
# in the paths this script builds.
contains() {
    for item in $1; do
        if [ "$item" = "$2" ]; then
            return 0
        fi
    done
    return 1
}

add_target() {
    if ! contains "$TARGETS" "$1"; then
        TARGETS="$TARGETS $1"
    fi
}

detect_targets() {
    claude_selected=0
    if [ -d "$HOME/.claude" ]; then
        add_target "$HOME/.claude/skills"
        claude_selected=1
    fi
    if [ -d "$HOME/.codex" ]; then
        add_target "$HOME/.codex/skills"
    fi
    # OpenCode reads ~/.claude/skills as well, so only add its own directory
    # when that shared location is not already covered.
    if [ "$claude_selected" -eq 0 ] && { [ -d "$HOME/.config/opencode" ] || [ -d "$HOME/.opencode" ]; }; then
        add_target "$HOME/.config/opencode/skills"
    fi
    if [ -d "$HOME/.agents" ]; then
        add_target "$HOME/.agents/skills"
    fi
    if [ -z "$TARGETS" ]; then
        log "No agent directory detected; defaulting to $HOME/.claude/skills."
        add_target "$HOME/.claude/skills"
    fi
}

is_our_checkout() {
    dest="$1"
    [ -f "$dest/SKILL.md" ] && [ -d "$dest/forge" ] && [ -d "$dest/grimoire" ]
}

install_one() {
    dest="$1/$NAME"
    if [ -d "$dest/.git" ]; then
        is_our_checkout "$dest" || fail "$dest is a git checkout of something else; remove it first"
        git -C "$dest" fetch --depth 1 origin "$REF" >/dev/null 2>&1 ||
            fail "could not fetch $REF from $REPO_URL"
        git -C "$dest" checkout --quiet --detach FETCH_HEAD
        log "updated  $dest"
        return
    fi
    if [ -e "$dest" ]; then
        fail "$dest already exists and is not a git checkout; remove it first"
    fi
    mkdir -p "$1"
    git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" "$dest" ||
        fail "could not clone $REPO_URL into $dest"
    log "installed $dest"
}

uninstall_one() {
    dest="$1/$NAME"
    if [ ! -e "$dest" ]; then
        return
    fi
    is_our_checkout "$dest" || fail "$dest does not look like an img2threejs checkout; not removing it"
    rm -rf "$dest"
    log "removed  $dest"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --target)
            [ $# -ge 2 ] || fail "--target needs a directory"
            add_target "$2"
            shift 2
            ;;
        --target=*)
            add_target "${1#--target=}"
            shift
            ;;
        --uninstall)
            MODE="uninstall"
            shift
            ;;
        --help | -h)
            usage
            exit 0
            ;;
        *)
            fail "unknown option: $1 (see --help)"
            ;;
    esac
done

command -v git >/dev/null 2>&1 || fail "git is required"

if [ -z "$TARGETS" ]; then
    detect_targets
fi

for target in $TARGETS; do
    if [ "$MODE" = "uninstall" ]; then
        uninstall_one "$target"
    else
        install_one "$target"
    fi
done

if [ "$MODE" = "uninstall" ]; then
    exit 0
fi

if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' ||
        log "warning: the pipeline scripts need Python 3.10 or newer"
else
    log "warning: python3 not found; the pipeline scripts need Python 3.10 or newer"
fi

cat <<'EOF'

Start a new agent session, then attach an object image and run:

  /img2threejs Rebuild this object as a Three.js model, keep the proportions, angles, and colours.
EOF
