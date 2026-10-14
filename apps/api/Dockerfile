# apps/api 배포 이미지. 빌드 컨텍스트는 이 폴더다 (deploy/docker/docker-compose.deploy.yml).
#
#   make api-image        굽기
#   make api-up / api-down / api-logs
#
# --workers 1 고정 — run 진행 상황이 프로세스 메모리에 있어서 (#134)
# 둘 이상이면 POST 받은 프로세스와 GET 받은 프로세스가 갈려 조용히 404 가 난다.

FROM python:3.12-slim

WORKDIR /app

# uv 설치 — 버전 고정 (빌드마다 같은 결과)
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

# 의존성 레이어 캐시: 매니페스트만 먼저 복사
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 소스 복사
COPY . .

# root 로 돌지 않는다 (웹 Dockerfile 과 같은 원칙)
RUN addgroup --gid 1001 --system apiuser \
 && adduser --uid 1001 --system --ingroup apiuser apiuser \
 && mkdir -p /tmp/uv-cache && chown apiuser:apiuser /tmp/uv-cache
ENV UV_CACHE_DIR=/tmp/uv-cache
USER apiuser

EXPOSE 8000

# --no-sync: 빌드 때 설치한 의존성을 그대로 쓴다. 없으면 기동마다 dev 그룹을 다시 받는다.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
