#!/usr/bin/env bash
# build-base-image.sh — rebuild the Incus base image used by hermes-ui to
# provision per-customer Hermes containers.
#
# Design: the image is intentionally THIN. It contains only the OS, Python
# runtime, an empty venv with the project deps pre-installed, the systemd unit,
# and the ops/ scripts. The actual code is NOT baked in — every (re)start of
# hermes-gateway.service runs ops/update-from-repo.sh which pulls the desired
# ref from origin. Re-run this script only when the OS/Python/deps base changes
# (rarely), not on every commit.
#
# Run this script ON THE INCUS TARGET HOST as root (or via sudo). Requires:
#   - incus CLI configured locally
#   - network access to debian images: and to the fork repo
#
# Usage:
#   ./build-base-image.sh                       # uses defaults
#   IMAGE_ALIAS=hermes ./build-base-image.sh    # publish under specific alias
#   REF=main ./build-base-image.sh              # which ref to seed with
#
# Outputs: a new image alias e.g. hermes-base-YYYYMMDD-HHMM, and (if
# IMAGE_ALIAS is set) atomically swings that alias to point at the new image.
# The previous image is kept (without the swapped alias) as fallback.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Jhonnyr97/hermes-agent.git}"
REF="${REF:-main}"
IMAGE_ALIAS="${IMAGE_ALIAS:-hermes}"
BASE_IMAGE="${BASE_IMAGE:-images:debian/12}"
STAMP="$(date -u +%Y%m%d-%H%M)"
BUILD_NAME="${BUILD_NAME:-hermes-base-build-$STAMP}"
NEW_ALIAS="${NEW_ALIAS:-hermes-base-$STAMP}"

# Layout INSIDE the resulting image — must match ops/hermes-gateway.service.
CODE_DIR=/opt/hermes-agent
VENV_DIR=/opt/hermes-agent/venv

log() { printf '[build-base-image] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

command -v incus >/dev/null || die "incus CLI not found in PATH"

log "config:"
log "  REPO_URL=$REPO_URL"
log "  REF=$REF"
log "  BASE_IMAGE=$BASE_IMAGE"
log "  BUILD_NAME=$BUILD_NAME"
log "  NEW_ALIAS=$NEW_ALIAS"
log "  swap-into-alias=$IMAGE_ALIAS"

# Clean up any stale build container.
if incus info "$BUILD_NAME" >/dev/null 2>&1; then
    log "deleting stale build container $BUILD_NAME"
    incus delete -f "$BUILD_NAME"
fi

log "launching build container $BUILD_NAME from $BASE_IMAGE"
incus launch "$BASE_IMAGE" "$BUILD_NAME"

# Wait for cloud-init / network inside the container.
log "waiting for container to be reachable"
for _ in $(seq 1 30); do
    if incus exec "$BUILD_NAME" -- true 2>/dev/null; then
        break
    fi
    sleep 1
done
log "waiting for cloud-init to finish"
incus exec "$BUILD_NAME" -- bash -c 'cloud-init status --wait 2>/dev/null || true'

# Provision the image.
log "installing OS packages"
incus exec "$BUILD_NAME" -- bash -c '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-venv python3-pip
rm -rf /var/lib/apt/lists/*
'

log "cloning $REF from $REPO_URL to bootstrap deps"
# We clone here only to grab pyproject/requirements so the venv has all deps
# pre-resolved. The code itself is then deleted: update-from-repo.sh will
# re-clone at first boot of every spawned customer.
incus exec "$BUILD_NAME" -- bash -c "
set -euo pipefail
mkdir -p $CODE_DIR
git clone --depth 1 --branch '$REF' '$REPO_URL' $CODE_DIR
python3 -m venv $VENV_DIR
$VENV_DIR/bin/pip install --upgrade --quiet pip
cd $CODE_DIR
if [ -f pyproject.toml ] || [ -f setup.py ]; then
    $VENV_DIR/bin/pip install --quiet -e .
else
    echo 'no pyproject.toml/setup.py — skipping pip install -e .'
fi
"

log "installing ops/ scripts and systemd unit"
incus exec "$BUILD_NAME" -- bash -c "
set -euo pipefail
install -d -m 755 $CODE_DIR/ops
install -m 755 $CODE_DIR/ops/update-from-repo.sh $CODE_DIR/ops/update-from-repo.sh
install -m 644 $CODE_DIR/ops/hermes-gateway.service /etc/systemd/system/hermes-gateway.service
systemctl daemon-reload
systemctl enable hermes-gateway.service
"

log "scrubbing baked code — only ops/, venv/, .git stay; everything else will"
log "be re-fetched on first boot by ExecStartPre"
incus exec "$BUILD_NAME" -- bash -c "
set -euo pipefail
cd $CODE_DIR
# Remove the working copy contents EXCEPT venv/, ops/, .git/
find . -mindepth 1 -maxdepth 1 \
    ! -name venv \
    ! -name ops \
    ! -name .git \
    -exec rm -rf {} +
# Also reset the git index so a 'git status' inside the spawned container
# doesn't show a million deleted files — the next reset --hard FETCH_HEAD
# (run by update-from-repo.sh) restores everything.
git -c safe.directory=$CODE_DIR reset --hard HEAD
"

log "stopping container before snapshot"
incus stop "$BUILD_NAME"

log "publishing as alias $NEW_ALIAS"
incus publish "$BUILD_NAME" --alias "$NEW_ALIAS" \
    description="hermes base image (thin), seeded from $REPO_URL@$REF on $STAMP"
NEW_FP="$(incus image list "$NEW_ALIAS" -f csv -c f | head -1)"
log "new image fingerprint: $NEW_FP"

# Atomic-ish alias swap: the previous image keeps its own fingerprint and any
# other aliases, just loses the shared alias name.
if incus image alias list -f csv | awk -F, -v a="$IMAGE_ALIAS" '$1==a {found=1} END{exit !found}'; then
    PREV_FP="$(incus image alias list -f csv | awk -F, -v a="$IMAGE_ALIAS" '$1==a {print $2}')"
    log "alias $IMAGE_ALIAS currently -> $PREV_FP; removing it before re-creating"
    incus image alias delete "$IMAGE_ALIAS"
    log "to rollback: incus image alias delete $IMAGE_ALIAS && incus image alias create $IMAGE_ALIAS $PREV_FP"
fi
incus image alias create "$IMAGE_ALIAS" "$NEW_FP"
log "alias $IMAGE_ALIAS now -> $NEW_FP"

log "deleting build container $BUILD_NAME"
incus delete "$BUILD_NAME"

log "done. new customers spawned with image '$IMAGE_ALIAS' will pull $REF from"
log "$REPO_URL on first hermes-gateway start."
