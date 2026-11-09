#!/usr/bin/env bash
# api 상자만 EC2 메타데이터(169.254.169.254)에 닿게 하는 서버 방화벽 규칙.
#
# 서버 역할(EC2 인스턴스 역할)의 임시 열쇠는 메타데이터 주소에서 나온다. api 는 사진을 S3 에서 꺼내려고
# 그 열쇠가 필요하다 (apps/api/app/integrations/photo_uploads.py). 도커 상자는 서버보다 한 칸 안쪽이라
# EC2 의 hop limit 을 2 로 올려야 열쇠가 닿는데, 그러면 web · caddy · db · redis 상자도 닿는다.
# 서버 역할은 권한이 넓어서(PowerUserAccess) 그 상자 하나가 뚫리면 AWS 계정까지 위험하다.
# 그래서 도커 상자 → 메타데이터 길을 막고 api 상자 주소만 연다.
#
#   사용: deploy/scripts/imds-firewall.sh [--install | --check]   (root)
#     (없음)     규칙을 건다. 여러 번 돌려도 규칙은 한 벌이다
#     --install  규칙을 걸고, 서버가 재부팅해도 다시 걸리게 systemd 에 등록한다. `make deploy-up` 이 부른다
#     --check    지금 걸린 규칙만 보여 준다
#
# 🚨 순서: 이 규칙이 서버에 걸린 **뒤에** hop limit 을 2 로 올린다. 거꾸로 하면 그 사이 모든 상자가
#    열쇠를 받을 수 있다 (docs/notice/notice-ocr-v1.md §2 "배포 전에 할 것").
# 🚨 API_IP 는 docker-compose.deploy.yml 의 api 의 ktc4-edge 주소(prod 기본값)와 같아야 한다.
#    dev api 는 .21 이라 여기서 막힌다 (#747). api 가 ktc4-edge 로 나가게 하는 건 compose 의 gw_priority 다.
# 🚨 서버 자신이 메타데이터에 닿는 길(OUTPUT)은 건드리지 않는다 — SSM 접속 · CloudWatch 가 쓴다.
#    도커 상자에서 나가는 길(FORWARD → DOCKER-USER)만 막는다.
# 맥에서는 아무것도 하지 않는다 — iptables 도 메타데이터 주소도 없다.
set -euo pipefail

API_IP=172.30.0.20
IMDS=169.254.169.254
TAG=ktc4-imds
UNIT=/etc/systemd/system/ktc4-imds-firewall.service

if [ "$(uname -s)" != "Linux" ]; then
  echo "imds-firewall: Linux 가 아니라 건너뜀"
  exit 0
fi
if [ "$(id -u)" -ne 0 ]; then
  echo "imds-firewall: root 로 돌려야 한다 (sudo)" >&2
  exit 1
fi
if ! iptables -n -L DOCKER-USER >/dev/null 2>&1; then
  # 도커가 안 떠 있거나 iptables 를 쓰지 않는다. 조용히 넘어가면 hop limit 을 올렸을 때 열쇠가 샌다
  echo "imds-firewall: DOCKER-USER 체인이 없다 — 규칙을 걸지 못했다" >&2
  exit 1
fi

if [ "${1:-}" = "--check" ]; then
  iptables -S DOCKER-USER | grep -- "$TAG" || echo "imds-firewall: 규칙 없음"
  exit 0
fi

# 전에 건 규칙을 지우고 새로 건다 — 여러 번 돌려도 한 벌
while iptables -D DOCKER-USER -s "$API_IP/32" -d "$IMDS/32" \
  -m comment --comment "$TAG" -j ACCEPT 2>/dev/null; do :; done
while iptables -D DOCKER-USER -d "$IMDS/32" -m comment --comment "$TAG" -j DROP 2>/dev/null; do :; done
# 둘 다 맨 앞에 넣는다. 나중에 넣은 ACCEPT 가 DROP 보다 위에 온다
iptables -I DOCKER-USER 1 -d "$IMDS/32" -m comment --comment "$TAG" -j DROP
iptables -I DOCKER-USER 1 -s "$API_IP/32" -d "$IMDS/32" -m comment --comment "$TAG" -j ACCEPT
echo "imds-firewall: api($API_IP) 상자만 메타데이터에 닿게 했다"

if [ "${1:-}" = "--install" ]; then
  script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  cat >"$UNIT" <<EOF
[Unit]
Description=ktc4 - api 상자만 EC2 메타데이터에 닿게 하는 방화벽 규칙
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$script

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable ktc4-imds-firewall.service >/dev/null
  echo "imds-firewall: 재부팅 뒤에도 다시 걸리게 등록했다 ($UNIT)"
fi
