"""되묻기 답에 그 질문의 답이 아닌 말이 섞였는지 — `answer_leftover`.

Health 답 run 은 모델도 Memory 도 부르지 않아서, 답에 섞인 기록("응, 그리고 열 38도였어")을 가려
줄 쪽이 없다. 그 기록은 저장되지 않으니 코드가 알려야 한다. 판정은 일부러 보수적이다 — 짧은 확인
답("네 알겠어요")에 "반영하지 않았어요" 가 붙으면 보호자가 오히려 헷갈린다. 걸리는 것은 체온을
말했거나, 표(네 · 아니요 · 약 이름)에 걸리지 않는 글이 열 글자 넘게 남는 답이다.
"""

from uuid import uuid4

import pytest

from app.agents.common.result import DomainPending
from app.agents.health.labels.medication import answer_leftover

STOP = DomainPending(
    "health",
    "medication_stop",
    "항생제 복약 알림을 멈출까요?",
    {"schedule_id": str(uuid4()), "title": "항생제"},
)
WHICH = DomainPending(
    "health",
    "medication_which",
    "어느 약인가요?",
    {
        "op": "stop",
        "args": {},
        "candidates": [
            {"schedule_id": str(uuid4()), "title": "항생제"},
            {"schedule_id": str(uuid4()), "title": "해열제"},
        ],
    },
)
TIMING = DomainPending(
    "health",
    "dose_timing",
    "식사 전후 언제 먹이면 되나요?",
    {"op": "create", "timing_spans": ["하루 세 번"], "draft": {}},
)


@pytest.mark.parametrize(
    "answer",
    [
        "응",
        "네 알겠어요",
        "아니 그냥 둬",
        "아니요 그냥 둬요 계속 먹일게요",
        "항생제 멈춰줘",
        "응 그만 먹일래요",
    ],
)
def test_짧은_확인_답은_다른_내용이_아니다(answer: str) -> None:
    assert answer_leftover(STOP, [answer]) is False


@pytest.mark.parametrize(
    "answer",
    [
        "응 그리고 오늘 열 38도였어",
        "응 열 38도야",
        "네 그리고 어제부터 기침을 계속 했고 밥도 잘 안 먹어요",
    ],
)
def test_체온이나_긴_다른_말이_섞인_답은_다른_내용이다(answer: str) -> None:
    assert answer_leftover(STOP, [answer]) is True


def test_약_이름만_말한_답은_다른_내용이_아니다() -> None:
    assert answer_leftover(WHICH, ["항생제"]) is False
    assert answer_leftover(WHICH, ["항생제요"]) is False
    assert answer_leftover(WHICH, ["해열제 말고 항생제"]) is False


def test_약_이름_말고_체온이_섞이면_다른_내용이다() -> None:
    assert answer_leftover(WHICH, ["항생제요 근데 열 38.5도예요"]) is True


def test_시점_답은_체온이_섞일_때만_다른_내용이다() -> None:
    assert answer_leftover(TIMING, ["식후요"]) is False
    assert answer_leftover(TIMING, ["아침 점심 저녁 밥 먹고 30분 뒤에"]) is False
    assert answer_leftover(TIMING, ["식후요 열 38도"]) is True


def test_모르는_맥락이나_다른_Agent_의_맥락은_다른_내용이_아니다() -> None:
    other = DomainPending("growth", "ask.routine_current", "양치는요?", {})
    assert answer_leftover(other, ["응 그리고 오늘 열 38도였어"]) is False
    assert answer_leftover(DomainPending("health", "unknown", "?", {}), ["열 38도"]) is False


@pytest.mark.parametrize(
    "answer",
    [
        "네네 알겠습니다 감사합니다",
        "네 의사 선생님이 그만 먹이래요",
        "응 이제 다 나아서 그만 먹여도 될 것 같아",
    ],
)
def test_정중한_확인_답과_이유_설명은_다른_내용이_아니다(answer: str) -> None:
    """인사 · 이유는 저장할 기록이 아니다. 안내가 붙으면 중단이 안 됐다고 읽힌다."""
    assert answer_leftover(STOP, [answer]) is False


@pytest.mark.parametrize(
    "answer",
    [
        "응 그리고 어제부터 기침을 했어",
        "응 그리고 해열제 먹였어",
        "응 그리고 오늘 어린이집에서 친구랑 한참 놀았고 낮잠도 푹 잤어요",
    ],
)
def test_증상_복용_표현이나_긴_다른_말이_섞이면_다른_내용이다(answer: str) -> None:
    assert answer_leftover(STOP, [answer]) is True


def test_증상_낱말이_든_약_이름만_말한_답은_다른_내용이_아니다() -> None:
    """약 이름을 뺀 뒤에 판정한다. "기침약" 의 "기침" 은 증상 기록이 아니다."""
    which = DomainPending(
        "health",
        "medication_which",
        "어느 약인가요?",
        {
            "op": "stop",
            "args": {},
            "candidates": [
                {"schedule_id": str(uuid4()), "title": "기침약"},
                {"schedule_id": str(uuid4()), "title": "콧물 시럽"},
            ],
        },
    )
    stop = DomainPending(
        "health", "medication_stop", "?", {"schedule_id": str(uuid4()), "title": "기침약"}
    )

    assert answer_leftover(which, ["기침약이요"]) is False
    assert answer_leftover(stop, ["네 기침약 끊어요"]) is False
