"""서버 계약(OpenAPI)을 JSON 파일로 쓴다 (#559).

    cd apps/api && uv run python -m scripts.dump_openapi ../web/.openapi.json

`/docs` 의 Swagger 가 보여 주는 것과 같은 `app.openapi()` 다. 웹이 손으로 적은 API 타입
(`apps/web/src/lib/api/types.ts`)이 이것과 어긋나는지 웹의 계약 비교 테스트가 읽는다
(`make web-contract` · `.github/workflows/ci-contract.yml`).

DB 에도 외부 API 에도 붙지 않는다. 다만 `app.main` 을 import 하면 설정 검사가 돌아서, `.env` 가
없는 곳(CI · 웹만 만지는 사람)에서는 필수 설정을 더미 값으로 채운 뒤 import 한다. 값을 바꿔도
OpenAPI 는 같다 (#560 리뷰).

🚨 **`.env` 가 있으면 채우지 않는다.** 환경변수가 `.env` 보다 앞서서, 채우면 `.env` 의 값을 덮는다.
   `.env` 가 있는데 필수 값이 빠졌으면 지금처럼 설정 오류로 멈추는 것이 맞다.
⚠️ `app/core/config.py` 의 필수 설정이 늘면 `_DUMMY_SETTINGS` 도 같이 늘린다 — 안 늘리면 `.env` 없는
   곳에서 `.env 설정 오류` 로 멈춘다.
"""

import argparse
import json
import os
from pathlib import Path

# app/core/config.py 의 _ENV_FILE 과 같은 자리 (apps/api/.env)
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

_DUMMY_SETTINGS = {
    "DB_HOST": "dummy",
    "DB_USER": "dummy",
    "DB_PASSWORD": "dummy",
    "DB_NAME": "dummy",
    "AUTH_RETURN_URL_WEB": "http://localhost:3000/auth/callback",
    "AUTH_RETURN_URL_APP": "icatch://auth",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="app.openapi() 를 JSON 파일로 쓴다")
    parser.add_argument("out", type=Path, help="쓸 파일 경로")
    args = parser.parse_args()

    if not _ENV_FILE.exists():
        for name, value in _DUMMY_SETTINGS.items():
            os.environ.setdefault(name, value)

    # 설정 검사가 import 시점에 돈다 — 위에서 채운 뒤에 불러야 한다.
    from app.main import app

    spec = app.openapi()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths, schemas = len(spec["paths"]), len(spec["components"]["schemas"])
    print(f"{args.out} — 경로 {paths}개, 스키마 {schemas}개")


if __name__ == "__main__":
    main()
