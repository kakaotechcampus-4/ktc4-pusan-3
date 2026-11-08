"""라이브 eval 이 남기는 실행 메타 — 측정 이력 행을 채울 값이 빠짐없이 든다 (#770).

LLM 을 부르지 않는다. 프롬프트를 바꾼 커밋마다 `docs/agents/eval/<이름>_EVAL.md` 의
측정 이력에 `커밋 · 지문 · 조건(모델/effort · 반복 횟수)` 을 적는데, 그 값을 손으로 옮기다
틀리지 않게 파일로 남긴다.
"""

import json
import re
from types import SimpleNamespace

import pytest

from app.agents.common.config import AgentSettings
from app.agents.prompt_registry import PROMPT_NAMES
from tests.eval.agents.run_meta import build_meta, write_live_meta


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        MEMORY_API_KEY="k",
        MEMORY_BASE_URL="https://gw.example.invalid/v1",
        MEMORY_MODEL="memory-model",
        MEMORY_REASONING_EFFORT="none",
        GROWTH_BASE_URL="https://gw.example.invalid/v1",
        GROWTH_MODEL="growth-model",
        GROWTH_REASONING_EFFORT="low",
    )


def test_메타에_측정_이력에_적을_값이_다_있다(monkeypatch):
    monkeypatch.setenv("TEST_REPEAT", "5")
    meta = build_meta(
        evalset=["tests/eval/agents/growth/test_growth_live.py"], settings=_settings()
    )

    assert set(meta) >= {
        "run_at",
        "commit",
        "dirty",
        "repetitions",
        "evalset",
        "models",
        "prompts",
    }
    assert meta["repetitions"] == 5
    assert meta["evalset"] == ["tests/eval/agents/growth/test_growth_live.py"]
    assert isinstance(meta["dirty"], bool)
    assert meta["commit"] is None or re.fullmatch(r"[0-9a-f]{40}", meta["commit"])


def test_반복_횟수_환경변수가_없으면_1이다(monkeypatch):
    monkeypatch.delenv("TEST_REPEAT", raising=False)
    assert build_meta(evalset=[], settings=_settings())["repetitions"] == 1


def test_역할마다_모델과_effort_가_실제_설정값이다():
    models = build_meta(evalset=[], settings=_settings())["models"]

    assert models["memory"] == {"model": "memory-model", "reasoning_effort": "none"}
    assert models["growth"] == {"model": "growth-model", "reasoning_effort": "low"}
    # growth 만 따로 정했으니 food 는 memory 설정을 따른다
    assert models["food"]["model"] == "memory-model"


def test_설정이_틀린_역할은_None_이고_나머지는_남는다():
    broken = AgentSettings(
        _env_file=None,
        MEMORY_API_KEY="k",
        MEMORY_BASE_URL="https://gw.example.invalid/v1",
        MEMORY_MODEL="memory-model",
        FOOD_MODEL="food-only-model",  # BASE_URL 없이 MODEL 만 있으면 AgentConfigError
    )
    models = build_meta(evalset=[], settings=broken)["models"]

    assert models["food"] is None
    assert models["memory"]["model"] == "memory-model"


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_프롬프트_10곳의_전체_지문이_든다(name):
    prompts = build_meta(evalset=[], settings=_settings())["prompts"]

    assert re.fullmatch(r"[0-9a-f]{64}", prompts[name])


def _item(nodeid: str, *, live: bool) -> SimpleNamespace:
    return SimpleNamespace(
        nodeid=nodeid, get_closest_marker=lambda name: object() if live and name == "live" else None
    )


def test_live_테스트가_돌았으면_메타_파일을_쓴다(tmp_path):
    path = tmp_path / "meta.json"
    items = [
        _item("tests/eval/agents/food/test_food_run.py::test_a", live=True),
        _item("tests/eval/agents/food/test_food_run.py::test_b", live=True),
        _item("tests/eval/agents/memory/test_x.py::test_c", live=False),
    ]

    assert write_live_meta(items, path) == path
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["evalset"] == ["tests/eval/agents/food/test_food_run.py"]


def test_live_테스트가_안_돌았으면_파일을_쓰지_않는다(tmp_path):
    path = tmp_path / "meta.json"

    assert write_live_meta([_item("tests/eval/agents/a.py::t", live=False)], path) is None
    assert not path.exists()
