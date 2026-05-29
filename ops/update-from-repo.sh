#!/usr/bin/env bash
# update-from-repo.sh — fetch the desired ref from origin, hard-reset the working
# copy, and run a sanity gate. Invoked by hermes-gateway.service as
# ExecStartPre so that every (re)start picks up the latest code.
#
# Behaviour:
#   - If $CODE_DIR is empty / not a git repo, clones $HERMES_REPO_URL into it.
#   - Always fetches $HERMES_REF (default: main) from origin and hard-resets.
#   - Runs `python -m py_compile` on a curated set of entry-point files; if any
#     fails, exits non-zero so systemd refuses to (re)start hermes-gateway.
#     Hermes stays on whatever it was running before — no crashloop on prod.
#
# Pinning / rollback for a single instance:
#   sudo systemctl edit hermes-gateway
#   [Service]
#   Environment=HERMES_REF=<good-sha-or-branch>
#   sudo systemctl restart hermes-gateway
#
# Environment variables (all optional):
#   HERMES_REPO_URL  default: https://github.com/Jhonnyr97/hermes-agent.git
#   HERMES_REF       default: main
#   CODE_DIR         default: /opt/hermes-agent
#   VENV_DIR         default: $CODE_DIR/venv
#   SKIP_UPDATE      if "1", short-circuits the fetch+reset (useful for debug).

set -euo pipefail

HERMES_REPO_URL="${HERMES_REPO_URL:-https://github.com/Jhonnyr97/hermes-agent.git}"
HERMES_REF="${HERMES_REF:-main}"
CODE_DIR="${CODE_DIR:-/opt/hermes-agent}"
VENV_DIR="${VENV_DIR:-$CODE_DIR/venv}"
SKIP_UPDATE="${SKIP_UPDATE:-0}"

log() { printf '[update-from-repo] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

if [ "$SKIP_UPDATE" = "1" ]; then
    log "SKIP_UPDATE=1 — leaving working copy untouched"
else
    log "ref=$HERMES_REF repo=$HERMES_REPO_URL code_dir=$CODE_DIR"
    mkdir -p "$CODE_DIR"

    # Bootstrap: if CODE_DIR is empty or missing .git, clone fresh.
    # Preserves $VENV_DIR if it sits inside CODE_DIR.
    if [ ! -d "$CODE_DIR/.git" ]; then
        log "no .git in $CODE_DIR — cloning fresh from $HERMES_REPO_URL"
        TMP_CLONE="$(mktemp -d)"
        git clone --depth 1 --branch "$HERMES_REF" "$HERMES_REPO_URL" "$TMP_CLONE/repo"
        # Move .git in first, then everything else, skipping files we want to keep.
        mv "$TMP_CLONE/repo/.git" "$CODE_DIR/"
        # Use rsync-like behaviour with cp -a + skipping the venv dir name.
        for entry in "$TMP_CLONE/repo/".* "$TMP_CLONE/repo/"*; do
            base="$(basename "$entry")"
            [ "$base" = "." ] || [ "$base" = ".." ] && continue
            [ "$base" = ".git" ] && continue
            # Don't clobber the venv dir if it lives inside CODE_DIR.
            if [ -e "$CODE_DIR/$base" ] && [ "$CODE_DIR/$base" = "$VENV_DIR" ]; then
                continue
            fi
            cp -a "$entry" "$CODE_DIR/"
        done
        rm -rf "$TMP_CLONE"
        # Make sure the remote URL is what we expect (in case clone added auth).
        git -C "$CODE_DIR" remote set-url origin "$HERMES_REPO_URL"
    fi

    # Always work as if /opt/hermes-agent is owned by us (avoid "dubious
    # ownership" rejections when systemd ran git as a different uid).
    git -C "$CODE_DIR" config --local --replace-all safe.directory "$CODE_DIR" || true

    log "fetching origin/$HERMES_REF"
    GIT_TERMINAL_PROMPT=0 git -C "$CODE_DIR" fetch --no-tags --no-progress --depth 1 origin "$HERMES_REF"

    log "resetting working copy to FETCH_HEAD"
    git -C "$CODE_DIR" reset --hard FETCH_HEAD

    HEAD_SHA="$(git -C "$CODE_DIR" rev-parse --short HEAD)"
    log "now at $HEAD_SHA"
fi

# Sanity gate: refuse to start if the key entry-point files don't compile.
# Add files here when you discover new boot-time imports.
SANITY_FILES=(
    "gateway/platforms/api_server.py"
    "cron/scheduler.py"
)

PY="$VENV_DIR/bin/python"
[ -x "$PY" ] || die "python interpreter not found at $PY"

log "sanity gate: py_compile ${#SANITY_FILES[@]} file(s)"
for f in "${SANITY_FILES[@]}"; do
    if [ ! -f "$CODE_DIR/$f" ]; then
        log "WARN: $f not present in this ref — skipping"
        continue
    fi
    if ! "$PY" -m py_compile "$CODE_DIR/$f"; then
        die "py_compile failed on $f — hermes-gateway will NOT be (re)started"
    fi
done

log "sanity gate passed — handing off to ExecStart"
