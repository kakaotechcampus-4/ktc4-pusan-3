"""라이브 eval 실행 메타 (#770) — 측정 이력 행에 적을 값을 파일로 남긴다.

`conftest.py` 가 `-m live` 로 돈 세션이 끝나면 `build_meta` 를 `live_run_meta.json` 으로 쓴다
(`.gitignore` 대상). pytest 가 아닌 eval 스크립트(급식표 · 알림장 OCR, 판정기 실험)는 돌린 뒤
직접 남긴다:

    uv run python -m tests.eval.agents.run_meta scripts/meal_ocr_eval.py
 프롬프트 본문 · 질문 · 답은 넣지 않는다 — 이름과 SHA-256, 모델, 커밋뿐이다.

- `commit` · `dirty`: 평가한 코드. 작업 트리가 더러우면 측정 이력 행의 커밋 옆에 `dirty` 를 적는다
- `models`: 역할마다 실제로 쓴 `<ROLE>_MODEL` · `<ROLE>_REASONING_EFFORT`.
  `.env` 값이라 이 파일이 아니면 남지 않는다. 설정이 틀린 역할은 `None`
- `repetitions`: 같은 입력을 몇 번 돌렸나 (`TEST_REPEAT`, 없으면 1)
- `prompts`: 10곳의 전체 지문. 문서에는 앞 12자를 적는다
"""

import argparse
import json
import os
import subprocess
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from app.agents.common.config import AgentConfigError, AgentSettings, get_agent_settings
from app.agents.prompt_registry import all_fingerprints
from app.core.agent_config import AGENT_ROLES

API_DIR = Path(__file__).resolve().parents[3]
META_PATH = Path(__file__).with_name("live_run_meta.json")


@cache
def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(  # noqa: S603  (고정 인자만 쓴다)
            ["git", *args],  # noqa: S607
            cwd=API_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip()


def _models(settings: AgentSettings) -> dict[str, dict[str, str] | None]:
    models: dict[str, dict[str, str] | None] = {}
    for role in AGENT_ROLES:
        name = role.lower()
        try:
            profile = settings.profile(name)  # type: ignore[arg-type]
        except (AgentConfigError, ValueError):
            models[name] = None
            continue
        models[name] = {"model": profile.model, "reasoning_effort": profile.reasoning_effort}
    return models


def _all_models(settings: AgentSettings, ocr_model: str | None) -> dict[str, Any]:
    """역할 6개 + 판정기 + OCR 두 곳. 판정기 · OCR 은 effort 를 역할 설정으로 두지 않아 None."""
    models: dict[str, Any] = dict(_models(settings))
    models["curator_judge"] = {
        "model": settings.CURATOR_JUDGE_MODEL or None,
        "reasoning_effort": None,
    }
    for name in ("meal_ocr", "notice_ocr"):  # 알림장 OCR 은 급식표 OCR 의 모델 설정을 같이 쓴다
        models[name] = {"model": ocr_model, "reasoning_effort": None}
    return models


def _ocr_model() -> str | None:
    try:
        from app.core.config import Settings

        return Settings().MEAL_OCR_MODEL  # type: ignore[call-arg]
    except Exception:  # 설정이 없으면 None — 메타 때문에 eval 이 실패하지 않게
        return None


def build_meta(
    *,
    evalset: Iterable[str],
    settings: AgentSettings | None = None,
    ocr_model: str | None = None,
) -> dict[str, Any]:
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(status),
        "repetitions": int(os.getenv("TEST_REPEAT", "1")),
        "evalset": sorted(evalset),
        "models": _all_models(settings or get_agent_settings(), ocr_model or _ocr_model()),
        "prompts": {name: print_.sha256 for name, print_ in all_fingerprints().items()},
    }


def write_live_meta(items: Iterable[Any], path: Path = META_PATH) -> Path | None:
    """이번 세션에서 `live` 테스트가 돌았으면 메타를 쓴다. 안 돌았으면 아무것도 하지 않는다."""
    files = {item.nodeid.split("::")[0] for item in items if item.get_closest_marker("live")}
    if not files:
        return None
    path.write_text(
        json.dumps(build_meta(evalset=files), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """pytest 밖에서 돈 eval 의 메타를 남긴다. 평가셋(돌린 스크립트 · 데이터) 경로를 받는다."""
    parser = argparse.ArgumentParser(description="라이브 eval 실행 메타를 남긴다 (#770)")
    parser.add_argument("evalset", nargs="+", help="돌린 eval 스크립트 · 평가셋 경로")
    parser.add_argument("--out", type=Path, default=META_PATH, help="쓸 파일")
    args = parser.parse_args(argv)
    meta = build_meta(evalset=args.evalset)
    args.out.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.out} 에 남겼다 — commit {meta['commit']} · dirty {meta['dirty']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
