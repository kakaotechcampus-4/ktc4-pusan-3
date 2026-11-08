"""프롬프트 지문 — 10곳 전부 만들어지고, 프롬프트 · tool spec 이 바뀌면 바뀐다 (#770)."""

import dataclasses
import re

import pytest

from app.agents import prompt_registry
from app.agents.common import prompt_fingerprint as pf
from app.core.prompt_hash import fingerprint

EXPECTED_NAMES = (
    "supervisor",
    "memory",
    "food",
    "activity",
    "activity_event_schedule",
    "growth",
    "health",
    "curator_judge",
    "meal_ocr",
    "notice_ocr",
)


def test_프롬프트는_10곳이다():
    assert prompt_registry.PROMPT_NAMES == EXPECTED_NAMES


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_전부_지문이_만들어진다(name):
    printed = pf.prompt_fingerprint(name)
    assert printed.name == name
    assert re.fullmatch(r"[0-9a-f]{64}", printed.sha256)
    assert re.fullmatch(r"[0-9a-f]{12}", printed.short)


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_두_번_만들어도_같은_지문이다(name):
    assert pf.prompt_fingerprint(name) == pf.prompt_fingerprint(name)


def test_모르는_이름은_거절한다():
    with pytest.raises(KeyError):
        pf.prompt_fingerprint("nope")


def test_이름마다_지문이_다르다():
    shas = {pf.prompt_fingerprint(name).sha256 for name in EXPECTED_NAMES}
    assert len(shas) == len(EXPECTED_NAMES)


def test_system_프롬프트가_바뀌면_지문이_바뀐다(monkeypatch):
    before = pf.prompt_fingerprint("supervisor")
    monkeypatch.setattr("app.agents.supervisor.prompt.build_system_prompt", lambda: "다른 프롬프트")
    assert pf.prompt_fingerprint("supervisor") != before


@pytest.mark.parametrize(
    ("name", "module"),
    [
        ("memory", "app.agents.memory.schemas.tool_defs"),
        ("food", "app.agents.food.schemas.tool_defs"),
        ("activity", "app.agents.activity.schemas.tool_defs"),
        ("growth", "app.agents.growth.schemas.tool_defs"),
        ("health", "app.agents.health.schemas.tool_defs"),
    ],
)
def test_tool_설명이_바뀌면_지문이_바뀐다(monkeypatch, name, module):
    import importlib

    mod = importlib.import_module(module)
    before = pf.prompt_fingerprint(name)
    first, *rest = mod.TOOL_DEFINITIONS
    changed = [dataclasses.replace(first, description=first.description + "!"), *rest]
    monkeypatch.setattr(mod, "TOOL_DEFINITIONS", changed)
    assert pf.prompt_fingerprint(name) != before


def test_supervisor_route_tool_이_바뀌면_지문이_바뀐다(monkeypatch):
    import copy

    from app.agents.supervisor import schemas

    before = pf.prompt_fingerprint("supervisor")
    spec = copy.deepcopy(schemas.ROUTE_TOOL_SPEC)
    spec["function"]["description"] += "!"
    monkeypatch.setattr(schemas, "ROUTE_TOOL_SPEC", spec)
    assert pf.prompt_fingerprint("supervisor") != before


@pytest.mark.parametrize(
    ("name", "module", "attr"),
    [
        ("activity_event_schedule", "app.agents.activity.event_schedule", "SYSTEM_PROMPT"),
        ("curator_judge", "app.agents.curator.embedding.judge", "PROMPT"),
        ("meal_ocr", "app.providers.meal_plan.image_ocr", "SYSTEM_PROMPT"),
        ("notice_ocr", "app.providers.notice.extractor", "SYSTEM_PROMPT"),
        ("notice_ocr", "app.providers.notice.extractor", "IMAGE_ADDENDUM"),
    ],
)
def test_상수_프롬프트가_바뀌면_지문이_바뀐다(monkeypatch, name, module, attr):
    import importlib

    mod = importlib.import_module(module)
    before = pf.prompt_fingerprint(name)
    monkeypatch.setattr(mod, attr, getattr(mod, attr) + "!")
    assert pf.prompt_fingerprint(name) != before


def test_응답_스키마가_바뀌면_지문이_바뀐다(monkeypatch):
    from app.agents.activity import event_schedule

    before = pf.prompt_fingerprint("activity_event_schedule")
    monkeypatch.setattr(event_schedule, "RESPONSE_FORMAT", {"type": "json_object"})
    assert pf.prompt_fingerprint("activity_event_schedule") != before


def test_급식표_OCR_지문은_provider_가_로그에_쓰는_값과_같다():
    from app.providers.meal_plan import image_ocr

    assert pf.prompt_fingerprint("meal_ocr").sha256 == fingerprint(*image_ocr.prompt_parts())


def test_알림장_OCR_지문은_provider_가_로그에_쓰는_값과_같다():
    from app.providers.notice import extractor

    assert pf.prompt_fingerprint("notice_ocr").sha256 == fingerprint(*extractor.prompt_parts())


def test_판정기_지문은_판정_기준_문장과_선택지_설명_틀로_만든다():
    from app.agents.curator.embedding import judge

    assert pf.prompt_fingerprint("curator_judge").sha256 == fingerprint(*judge.prompt_parts())


def test_growth_는_task_와_상태_구획을_전부_싣는다():
    parts = pf.prompt_parts("growth")
    joined = "\n".join(parts)
    # 교육 · 루틴 · 도서 구획이 모두 들어간다
    for needle in ("[교육 활동]", "[생활 루틴]", "[도서]"):
        assert needle in joined, needle


def test_activity_는_날씨_조건_구획을_전부_싣는다():
    joined = "\n".join(pf.prompt_parts("activity"))
    assert "날씨는 확인하지 않았다" in joined
    assert "오늘은 바깥 활동을 할 수 있다" in joined
    assert "오늘은 바깥 활동이 어렵다" in joined


def test_지문은_현재_시각에_기대지_않는다(monkeypatch):
    import datetime as dt

    before = pf.prompt_fingerprint("memory")

    class _Later(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2031, 5, 5, tzinfo=tz)

    monkeypatch.setattr(dt, "datetime", _Later)
    assert pf.prompt_fingerprint("memory") == before


def test_로그용_지문은_이름마다_한_번만_계산한다(monkeypatch):
    pf.logged_fingerprint.cache_clear()
    calls = []
    real = pf.prompt_fingerprint

    def counting(name):
        calls.append(name)
        return real(name)

    monkeypatch.setattr(pf, "prompt_fingerprint", counting)
    pf.logged_fingerprint("supervisor")
    pf.logged_fingerprint("supervisor")
    assert calls == ["supervisor"]
    pf.logged_fingerprint.cache_clear()


# ── 리뷰 반영: 모델에 가는 글이 지문 밖에 남지 않는다 ────────────────────────


def test_memory_응답_스키마가_바뀌면_지문이_바뀐다(monkeypatch):
    from app.agents.memory.schemas import reply

    before = pf.prompt_fingerprint("memory")
    monkeypatch.setattr(reply, "REPLY_FORMAT", {"type": "json_object"})
    assert pf.prompt_fingerprint("memory") != before


def test_memory_이어받기_응답_스키마가_바뀌면_지문이_바뀐다(monkeypatch):
    from app.agents.memory.schemas import reply

    before = pf.prompt_fingerprint("memory")
    monkeypatch.setattr(reply, "CONTINUATION_REPLY_FORMAT", {"type": "json_object"})
    assert pf.prompt_fingerprint("memory") != before


@pytest.mark.parametrize("attr", ["_EMPTY_TURN", "_PENDING_RETRY", "_HINT_HEADER", "_HAZARD"])
def test_memory_가_모델에_보내는_재시도_문구가_바뀌면_지문이_바뀐다(monkeypatch, attr):
    from app.agents.memory import agent

    before = pf.prompt_fingerprint("memory")
    monkeypatch.setattr(agent, attr, getattr(agent, attr) + "!")
    assert pf.prompt_fingerprint("memory") != before


def test_판정기_기준_문구가_바뀌면_지문이_바뀐다(monkeypatch):
    from app.agents.curator.embedding import judge

    before = pf.prompt_fingerprint("curator_judge")
    monkeypatch.setattr(judge, "CRITERION_NONE", judge.CRITERION_NONE + "!")
    assert pf.prompt_fingerprint("curator_judge") != before


@pytest.mark.parametrize(
    ("name", "module", "func"),
    [
        ("food", "app.agents.food.prompt", "recall_message"),
        ("food", "app.agents.food.prompt", "request_message"),
        ("growth", "app.agents.growth.prompt", "recall_message"),
        ("growth", "app.agents.growth.prompt", "request_message"),
        ("activity", "app.agents.activity.prompt", "recall_message"),
        ("activity", "app.agents.activity.prompt", "request_message"),
        ("supervisor", "app.agents.supervisor.prompt", "reroute_feedback"),
    ],
)
def test_user_메시지_틀이_바뀌면_지문이_바뀐다(monkeypatch, name, module, func):
    import importlib

    mod = importlib.import_module(module)
    before = pf.prompt_fingerprint(name)
    original = getattr(mod, func)
    monkeypatch.setattr(mod, func, lambda *a, **k: original(*a, **k) + "!")
    assert pf.prompt_fingerprint(name) != before


@pytest.mark.parametrize("name", ["food", "activity", "growth", "health"])
def test_tool_루프의_재시도_문구가_바뀌면_지문이_바뀐다(monkeypatch, name):
    from app.agents.common import tool_loop

    before = pf.prompt_fingerprint(name)
    monkeypatch.setattr(tool_loop, "BAD_JSON", tool_loop.BAD_JSON + "!")
    assert pf.prompt_fingerprint(name) != before
