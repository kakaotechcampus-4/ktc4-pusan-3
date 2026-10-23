"""서버 계약(OpenAPI)을 JSON 파일로 쓴다 (#559).

    cd apps/api && uv run python -m scripts.dump_openapi ../web/.openapi.json

`/docs` 의 Swagger 가 보여 주는 것과 같은 `app.openapi()` 다. 웹이 손으로 적은 API 타입
(`apps/web/src/lib/api/types.ts`)이 이것과 어긋나는지 웹의 계약 비교 테스트가 읽는다
(`make web-contract` · `.github/workflows/ci-contract.yml`).

DB 에도 외부 API 에도 붙지 않는다. 다만 `app.main` 을 import 하면 설정 검사가 돌아서, `.env` 가
없는 곳(CI)에서는 필수 설정에 더미 값을 넣어 부른다.
"""

import argparse
import json
from pathlib import Path

from app.main import app


def main() -> None:
    parser = argparse.ArgumentParser(description="app.openapi() 를 JSON 파일로 쓴다")
    parser.add_argument("out", type=Path, help="쓸 파일 경로")
    args = parser.parse_args()

    spec = app.openapi()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths, schemas = len(spec["paths"]), len(spec["components"]["schemas"])
    print(f"{args.out} — 경로 {paths}개, 스키마 {schemas}개")


if __name__ == "__main__":
    main()
