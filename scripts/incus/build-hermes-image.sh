#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-hermes}"
HERMES_GIT_REF="${HERMES_GIT_REF:-$(git rev-parse --short=12 HEAD)}"
RELEASE="${RELEASE:-noble}"
ARCHITECTURE="${ARCHITECTURE:-x86_64}"
EXPORT_DIR="${EXPORT_DIR:-dist/incus}"
BUILD_DIR="${BUILD_DIR:-/tmp/hermes-incus-image}"
ROOTFS_DIR="${BUILD_DIR}/rootfs"
META_DIR="${BUILD_DIR}/metadata"

if [[ "${IMAGE_NAME}" == *aziendaos* ]]; then
  echo "IMAGE_NAME must stay product-neutral; got ${IMAGE_NAME}" >&2
  exit 2
fi

rm -rf "${BUILD_DIR}"
mkdir -p "${ROOTFS_DIR}" "${META_DIR}" "${EXPORT_DIR}"

sudo apt-get update
sudo apt-get install -y \
  mmdebstrap \
  rsync \
  squashfs-tools \
  xz-utils \
  tar \
  python3 \
  python3-venv \
  python3-pip

sudo mmdebstrap \
  --variant=minbase \
  --architectures=amd64 \
  --components=main,universe \
  --include=systemd-sysv,dbus,cloud-init,ca-certificates,curl,git,jq,build-essential,python3,python3-pip,python3-venv,ffmpeg,tesseract-ocr,tesseract-ocr-ita,libtesseract-dev,libleptonica-dev \
  "${RELEASE}" \
  "${ROOTFS_DIR}" \
  "http://archive.ubuntu.com/ubuntu"

sudo mkdir -p "${ROOTFS_DIR}/opt/hermes-agent/src" "${ROOTFS_DIR}/etc/hermes-agent" "${ROOTFS_DIR}/etc/systemd/system/multi-user.target.wants"
sudo rsync -a --delete --exclude .git ./ "${ROOTFS_DIR}/opt/hermes-agent/src/"

sudo chroot "${ROOTFS_DIR}" /bin/bash -lc '
set -euo pipefail
python3 -m venv /opt/hermes-agent/venv
/opt/hermes-agent/venv/bin/pip install --upgrade pip
/opt/hermes-agent/venv/bin/pip install --no-cache-dir -e /opt/hermes-agent/src "aiohttp>=3.13.3,<4" "markitdown[all]"
'

sudo tee "${ROOTFS_DIR}/etc/hermes-agent/config.yaml" >/dev/null <<'YAML'
platforms:
  api_server:
    enabled: true
    host: 0.0.0.0
    port: 8642
    key: ${API_SERVER_KEY}
YAML

sudo tee "${ROOTFS_DIR}/etc/hermes-agent/.env" >/dev/null <<'ENV'
API_SERVER_KEY=CHANGE_ME_AT_PROVISIONING
HERMES_API_KEY=CHANGE_ME_AT_PROVISIONING
OPENAI_API_KEY=CHANGE_ME_AT_PROVISIONING
ENV
sudo chmod 0600 "${ROOTFS_DIR}/etc/hermes-agent/.env"

sudo tee "${ROOTFS_DIR}/etc/systemd/system/hermes-gateway.service" >/dev/null <<'UNIT'
[Unit]
Description=Hermes Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HERMES_HOME=/etc/hermes-agent
EnvironmentFile=/etc/hermes-agent/.env
ExecStart=/opt/hermes-agent/venv/bin/hermes gateway run
Restart=always
RestartSec=5
WorkingDirectory=/etc/hermes-agent

[Install]
WantedBy=multi-user.target
UNIT

sudo ln -sf /etc/systemd/system/hermes-gateway.service "${ROOTFS_DIR}/etc/systemd/system/multi-user.target.wants/hermes-gateway.service"
sudo rm -rf "${ROOTFS_DIR}/tmp/"* "${ROOTFS_DIR}/var/tmp/"* "${ROOTFS_DIR}/root/.cache" "${ROOTFS_DIR}/var/lib/apt/lists/"*
sudo find "${ROOTFS_DIR}/var/log" -type f -exec truncate -s 0 {} + || true

cat >"${META_DIR}/metadata.yaml" <<YAML
architecture: "${ARCHITECTURE}"
creation_date: $(date +%s)
properties:
  description: "${IMAGE_NAME} Hermes runtime ${HERMES_GIT_REF}"
  os: "Ubuntu"
  release: "${RELEASE}"
  variant: "default"
  serial: "${HERMES_GIT_REF}"
templates: {}
YAML

sudo tar -C "${META_DIR}" -cJf "${EXPORT_DIR}/incus.tar.xz" metadata.yaml
sudo mksquashfs "${ROOTFS_DIR}" "${EXPORT_DIR}/rootfs.squashfs" -noappend -comp xz
sudo chown "$(id -u):$(id -g)" "${EXPORT_DIR}/incus.tar.xz" "${EXPORT_DIR}/rootfs.squashfs"

sha256sum "${EXPORT_DIR}/incus.tar.xz" "${EXPORT_DIR}/rootfs.squashfs" >"${EXPORT_DIR}/SHA256SUMS"

cat >"${EXPORT_DIR}/IMPORT.md" <<EOF
# Import ${IMAGE_NAME}

\`\`\`bash
incus image import incus.tar.xz rootfs.squashfs --alias ${IMAGE_NAME} --alias ${IMAGE_NAME}-${HERMES_GIT_REF}
\`\`\`
EOF

echo "Built Incus image files in ${EXPORT_DIR}:"
ls -lh "${EXPORT_DIR}"
