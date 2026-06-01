#!/usr/bin/env bash
# EC2 (Amazon Linux 2023 또는 Ubuntu 22.04) 최초 1회 실행
# 사용: SSH로 EC2 접속 후 → curl로 받아서 bash
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"

echo "→ 1) 시스템 패키지 업데이트"
if command -v dnf >/dev/null; then          # Amazon Linux 2023
  sudo dnf update -y
  sudo dnf install -y docker ruby wget git
elif command -v apt >/dev/null; then        # Ubuntu
  sudo apt update -y
  sudo apt install -y docker.io ruby-full wget git
fi

echo "→ 2) Docker 시작 + ec2-user/ubuntu에 권한"
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true

echo "→ 3) docker compose plugin"
DOCKER_CONFIG=${DOCKER_CONFIG:-$HOME/.docker}
mkdir -p "$DOCKER_CONFIG/cli-plugins"
curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
  -o "$DOCKER_CONFIG/cli-plugins/docker-compose"
chmod +x "$DOCKER_CONFIG/cli-plugins/docker-compose"
sudo cp "$DOCKER_CONFIG/cli-plugins/docker-compose" /usr/local/lib/docker/cli-plugins/ 2>/dev/null || true

echo "→ 4) CodeDeploy agent 설치"
cd /tmp
wget "https://aws-codedeploy-${REGION}.s3.${REGION}.amazonaws.com/latest/install" -O install
chmod +x ./install
sudo ./install auto
sudo systemctl enable --now codedeploy-agent

echo "→ 5) 배포 대상 폴더 준비"
sudo mkdir -p /opt/tastebridge
sudo chown "$USER":"$USER" /opt/tastebridge

echo ""
echo "✅ 완료. 'sudo systemctl status codedeploy-agent' 로 확인"
echo "⚠️  docker 그룹 적용을 위해 한 번 로그아웃/재접속 필요"