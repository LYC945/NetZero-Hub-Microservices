#!/usr/bin/env bash
# Installs Docker Engine + Compose plugin from Docker's official apt repository
# inside the fresh NetZeroE virtual machine (WSL2, Ubuntu 24.04). Run as root.
set -euo pipefail

apt-get update -y
apt-get install -y ca-certificates curl rsync python3
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker version --format 'Docker Engine {{.Server.Version}}'
docker compose version
