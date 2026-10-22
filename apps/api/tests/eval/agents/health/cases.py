"""Health 라이브 eval 의 케이스 표.

입력 문장은 `docs/agents/health/test_input_health.txt` 의 `[Txx]` 가 정본이고, 기대는
`test_expected_health.md` 가 정본이다. 문서에 입력이 더해졌는데 이 표를 안 고치면
`test_health_eval_cases.py` 가 깨진다.

판정은 완성된 경로만 쓴다 — 동의 철회(`consent_closed`) · `schedule_check` · 증상 반복 hook ·
`medication` · `care_handoff` · `visit_summary`. 나머지는 `pending` 이라 라벨이 구현되기 전에는 GAP,
구현된 뒤에는 판정을 쓰라고 실패한다.

되묻고 멈추는 케이스는 `followups` 로 다음 턴 입력을 이어 돈다 — 앞 턴 결과의 `pending` 을 다음 턴
컨텍스트에 넣고 now 를 2분 뒤로 둔다(연결 이슈의 `reply_to` 를 흉내 낸다). 이어받는 턴은 모델 0회다.
검진 맥락 요약(T34 · T35)은 `schedule_check` 의 context 분기라 `checkup_context` 로 판정한다.

`memory=True` 케이스는 Health 가 아니라 **Memory 저장**을 본다(T21 먹인 약 행) — 먹인 약 칸은 이
이슈가 Memory 에 만든 것이라 같은 표에 둔다. 문서에 없는 변형 입력은 `text` 에 둔다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import time
from pathlib import Path

from app.agents.health.schemas.task import HealthTaskType
from tests.eval.agents.health.judges import (
    ANY,
    Ran,
    Verdict,
    check_on_screen,
    checkup_context,
    consent_closed,
    dose_status,
    emergency_card,
    fever_timeline,
    handoff_notice,
    med_create,
    med_no_advice,
    med_stop,
    med_stop_which,
    med_timing,
    med_update,
    memory_given,
    no_active_med,
    pending,
    schedule_check,
    symptom_repeat,
    visit_no_record,
    visit_timeline,
)

HEALTH_DOCS = Path(__file__).resolve().parents[6] / "docs" / "agents" / "health"

V = HealthTaskType.VISIT_SUMMARY
S = HealthTaskType.SCHEDULE_CHECK
P = HealthTaskType.PLACE_LOOKUP
M = HealthTaskType.MEDICATION
H = HealthTaskType.CARE_HANDOFF


@dataclass(frozen=True)
class MemorySeed:
    """Memory 저장 케이스가 돌기 전에 저장소에 넣어 두는 건강 기록 한 건 (오늘 · 지어낸 말)."""

    raw_text: str
    symptom: tuple[str, ...]


# T21-memory 앞에 둔 열 기록 — 따로 말한 "해열제 먹였어" 가 이 행에 이어지면 안 된다. 빈 저장소면
# 이을 대상이 없어 "따로 말한 약은 연결 안 됨" 이 공허하게 통과한다
PRIOR_FEVER = MemorySeed(raw_text="새벽에 열이 났어", symptom=("발열",))


@dataclass(frozen=True)
class Case:
    case_id: str  # "T13" · "T24-noconsent"
    input_id: str  # "T13"
    label: HealthTaskType | None  # None = hook 케이스 · Memory 저장 케이스
    world: str
    expect: str  # test_expected_health.md 의 "기대" 한 줄
    judge: Callable[[Ran], Verdict]
    night: bool = False
    followups: tuple[str, ...] = ()  # 되묻기에 답하는 다음 턴 입력 — 앞 턴의 pending 을 이어받는다
    memory: bool = False  # Health 가 아니라 Memory 저장을 돌린다 (T21 먹인 약)
    text: str | None = None  # 문서에 없는 변형 입력. None 이면 문서의 [Txx] 입력
    memory_seed: tuple[MemorySeed, ...] = ()  # Memory 저장 케이스가 돌기 전에 저장소에 둔 기록


def doc_inputs() -> dict[str, str]:
    """`test_input_health.txt` 의 [Txx] 입력. 문서가 정본이다."""
    lines = (HEALTH_DOCS / "test_input_health.txt").read_text(encoding="utf-8").splitlines()
    parsed: dict[str, str] = {}
    for line in lines:
        if line.startswith("[T") and "] " in line:
            key, text = line[1:].split("] ", 1)
            parsed[key] = text
    return parsed


# Health 로 오지 않는 입력 → 이유. SKIP 으로 남아 빠진 이유가 결과에 보인다
OUTSIDE: dict[str, str] = {
    "T06": "처방전 OCR 경로 — 이번 묶음 밖",
    "T17": "진단 · 교차투약 문의(S?) — Supervisor 연결 이슈",
    "T20": "Memory 체온 기록 — 연결 이슈",
    "T26": "진단 문의(S?) — Supervisor 연결 이슈",
    "T27": "진단 문의(S?) — Supervisor 연결 이슈",
    "T28": "Memory 관찰 — 연결 이슈",
    "T29": "알레르기 등록 안내 — Supervisor",
    "T36": "신체 성장 — Growth growth_review",
    "T37": "신체 성장 — Growth growth_review",
    "T38": "신체 성장 — Growth growth_review",
    "T39": "신체 성장 — Growth growth_review",
    "T40": "Memory child_growth_log — 연결 이슈",
    "T43": "응급 사전검사(S!) — Supervisor 연결 이슈",
    "T44": "응급 사전검사(S!) — Supervisor 연결 이슈",
    "T45": "응급 사전검사(S!) — Supervisor 연결 이슈",
}

_CONSENT = "blocked.consent 글자 그대로 · 모델 호출 안 함 · 건강정보를 읽지 않음"


def _case(
    case_id: str,
    label: HealthTaskType | None,
    world: str,
    expect: str,
    *,
    judge: Callable[[Ran], Verdict] | None = None,
    night: bool = False,
    followups: tuple[str, ...] = (),
) -> Case:
    return Case(
        case_id=case_id,
        input_id=case_id.split("-")[0],
        label=label,
        world=world,
        expect=expect,
        judge=judge or pending(label.value if label else "hook"),
        night=night,
        followups=followups,
    )


def _memory(
    case_id: str,
    expect: str,
    judge: Callable[[Ran], Verdict],
    *,
    text: str | None = None,
    seed: tuple[MemorySeed, ...] = (),
) -> Case:
    """Memory 저장 케이스 — 해열제는 C48 에 등록되지 않았다(항생제만)."""
    return Case(
        case_id=case_id,
        input_id=case_id.split("-")[0],
        label=None,
        world="C48",
        expect=expect,
        judge=judge,
        memory=True,
        text=text,
        memory_seed=seed,
    )


def _closed(case_id: str, label: HealthTaskType, detail: str = "") -> Case:
    expect = f"{_CONSENT} · {detail}" if detail else _CONSENT
    return _case(case_id, label, "C48-noconsent", expect, judge=consent_closed)


CASES: list[Case] = [
    # ── 복약 ── (T01–T05 · T12–T16 은 모델 한 번, 나머지는 모델 0회. 되묻기는 followups)
    _case(
        "T01",
        M,
        "C48",
        '초안 1장 (op="create") · dose 3 · notice_times=["08:30","12:30","19:30"] · '
        'title="항생제" · dosage="5ml" · indication_text=null · '
        'missing=["ends_on"](발화에 기간 없음) · 저장 0건',
        judge=med_create(("08:30", "12:30", "19:30"), dosage="5ml", missing=("ends_on",)),
    ),
    _case(
        "T02",
        M,
        "C48",
        '초안 0장 · "식사 전후 언제 먹이면 되나요?" → 다음 턴 "식후요" 는 모델 없이 초안 1장 '
        '(08:30 · 12:30 · 19:30 · missing=["title","ends_on"])',
        judge=med_timing(then="answer"),
        followups=("식후요",),
    ),
    _case(
        "T02-unclear",
        M,
        "C48",
        '"식사 전후 언제 먹이면 되나요?" → 다음 턴 "몰라" 는 med.timing_unclear · 초안 0 · '
        "쓰기 0 · 다시 묻지 않음",
        judge=med_timing(then="unclear"),
        followups=("몰라",),
    ),
    _case(
        "T03",
        M,
        "C48",
        'fixed 15:00 · notice_times=["15:00"] · missing=["ends_on"]',
        judge=med_create(("15:00",), missing=("ends_on",), title=ANY),
    ),
    _case(
        "T04",
        M,
        "C48",
        'breakfast −30 → notice_times=["07:30"] · missing=["ends_on"]',
        judge=med_create(("07:30",), missing=("ends_on",), title=ANY),
    ),
    _case(
        "T05",
        M,
        "C48",
        '초안의 indication_text에 "중이염" 원문 span · ends_on=오늘 포함 5일(+4일) · missing=[] '
        "(Memory 관찰 저장은 Health 밖)",
        judge=med_create(
            ("08:30", "12:30", "19:30"), indication="중이염", ends_in_days=4, missing=()
        ),
    ),
    _case("T07", M, "C48", "쓰기 0 · 모델 0회 · dose.check_on_screen", judge=check_on_screen()),
    _case("T07-nomed", M, "C48-nomed", "복약이 없으면 notice.no_active_med", judge=no_active_med()),
    _case("T08", M, "C48", "쓰기 0 · 모델 0회 · dose.check_on_screen", judge=check_on_screen()),
    _case("T09", M, "C48", "쓰기 0 · 모델 0회 · dose.check_on_screen", judge=check_on_screen()),
    _case(
        "T10",
        M,
        "C48",
        "dose.status (마지막 체크 08:30 회차 · 다음 예정 19:30 · 오늘 체크 1/3회) · 모델 0회",
        judge=dose_status(),
    ),
    _case("T11", M, "C48", "dose.status만 · 허가 문구 없음 · can_take 없음", judge=dose_status()),
    _case(
        "T12",
        M,
        "C48",
        '초안 op="update" · schedule_id · before에 기존 19:30 · 저녁 회차 fixed 19:00 · '
        "새 notice_times 08:30 · 12:30 · 19:00 · 제출 전 기존 코스 그대로",
        judge=med_update(("08:30", "12:30", "19:30"), ("08:30", "12:30", "19:00")),
    ),
    _case(
        "T13",
        M,
        "C48",
        "med.stop_confirm 되묻기 · 그 run 변경 0건 · 초안 없음 → '네' 다음 run 에서 "
        "status='stopped' (중단 시각 = 답한 run 의 now) · med.stopped · 모델 0회",
        judge=med_stop(answer="yes"),
        followups=("응",),
    ),
    _case(
        "T13-no",
        M,
        "C48",
        "med.stop_confirm 되묻기 → '아니요' 다음 run 은 변경 0건 · med.stop_kept",
        judge=med_stop(answer="no"),
        followups=("아니 그냥 둬",),
    ),
    _case(
        "T14",
        M,
        "C48",
        "약이 하나면 이름이 없어도 med.stop_confirm 되묻기 · 변경 0건",
        judge=med_stop(answer="ask"),
    ),
    _case(
        "T14-2med",
        M,
        "C48-2med",
        "어느 약인지 먼저 되묻기(변경 0건) → '항생제' 로 중단 확인 → '네' 로 중단",
        judge=med_stop_which(then="yes"),
        followups=("항생제", "응"),
    ),
    _case(
        "T14-2med-unclear",
        M,
        "C48-2med",
        "어느 약인지 되묻기 → 못 고른 답('글쎄')은 med.which_unclear · 쓰기 0 · 다시 묻지 않음",
        judge=med_stop_which(then="unclear"),
        followups=("글쎄",),
    ),
    _case(
        "T15",
        M,
        "C48",
        "med.stop_confirm(\"먹인 기록은 그대로 남아요\") → '네' 다음 run 에서 status='stopped'",
        judge=med_stop(answer="yes"),
        followups=("응",),
    ),
    _case(
        "T16",
        M,
        "C48",
        "용량 판단 없음 · 초안 없음 · 쓰기 0 · med.no_change 안내로 끝 (상담 안내는 Supervisor 몫)",
        judge=med_no_advice(),
    ),
    # 복약 라벨로 오면 지금은 약 이름을 보지 않고 체크 안내다(#544). 등록 여부로 Memory 와
    # 가르는 것은 연결 이슈 몫이라 이 판정은 그대로 둔다 — 갈림을 판정하지 않는다
    _case(
        "T21",
        M,
        "C48",
        "복약 라벨로 오면 쓰기 0 · 모델 0회 · dose.check_on_screen "
        "(등록 여부로 Memory 와 가르는 것은 연결 이슈)",
        judge=check_on_screen(),
    ),
    _memory(
        "T21-memory",
        "Memory — 등록 안 된 해열제: observation_health 약 행 1건(medication_given='해열제' · "
        "symptom 빈 배열 · 먹인 시각 오늘 03:00 · 연결 없음) · 증상 행 0건 "
        "(앞서 저장된 열 기록이 있어도 잇지 않는다)",
        memory_given(linked=False, given_at=time(3, 0)),
        seed=(PRIOR_FEVER,),
    ),
    _memory(
        "T21-linked",
        "Memory — 증상 행 1건(발열) + 약 행 1건(medication_given='해열제') · "
        "약 행의 linked_symptom_id = 그 증상 행",
        memory_given(linked=True),
        text="열이 끓길래 바로 해열제 먹였어",
    ),
    _closed("T01-noconsent", M, "초안 0장"),
    # ── 전달 서류 ──
    _case(
        "T18",
        H,
        "C48",
        '고정 서식 · 약·시각·보관·이유(있으면)·특이사항 · "보호자 확인 후 서명해 주세요"',
        judge=handoff_notice(),
    ),
    _case("T18-nomed", H, "C48-nomed", "notice.no_active_med", judge=no_active_med()),
    _closed("T18-noconsent", H),
    _case(
        "T19",
        H,
        "C48-card",
        "응급 카드 — 알레르기·만성질환·복용 약 원문 · 연락처 줄 없음",
        judge=emergency_card(),
    ),
    _case(
        "T19-safetyfail",
        H,
        "C48-safetyfail",
        "응급 카드의 알레르기 · 만성질환 칸을 비우고 조회 실패를 명시",
        judge=emergency_card(safety_ok=False),
    ),
    # ── 열 · 증상 · 진료 준비 ──
    _case(
        "T22",
        V,
        "C48-fever",
        "fever_timeline · 모델 0회 — 48시간 체온 4줄(시간순 · 부위) · 먹인 약은 같은 말의 체온 줄 "
        "바로 아래 · 최고·최저와 시각 · 코스마다 마지막으로 체크된 회차(예정 시각) · 등록 회차상 "
        "다음 예정 · 체크 안 된 회차 · 계열 · 경과 해석 0건",
        judge=fever_timeline(),
    ),
    _case("T22-notemp", V, "C48", "fever.no_record 한 줄 · 모델 0회", judge=fever_timeline()),
    _case(
        "T22-C2",
        V,
        "C2",
        "emergency 가 맨 앞 (3개월 미만 발열) → 이어서 열 타임라인 · 모델 0회",
        judge=fever_timeline(emergency_first=True),
    ),
    _case(
        "T23",
        V,
        "C48",
        "symptom_timeline · 모델 1회 · 에피소드 단위 요약 · 해석 0건",
        judge=visit_timeline(),
    ),
    _case(
        "T24",
        V,
        "C48",
        "symptom_timeline · 모델 1회 · 모델은 key 만 고르고 줄은 코드가 기록에서 그린다 · "
        "모델 입력에 원문 · 약 이름 · 용량 0건 · 근거가 전부 조회한 기록 · 식사 3일 · 복약은 "
        "등록 원문 + 날짜별 체크 n/m회 · 해석 · '안 먹였' · 비율 0건 · 알레르기 줄은 코드",
        judge=visit_timeline(),
    ),
    _closed("T24-noconsent", V),
    _case(
        "T24-safetyfail",
        V,
        "C48-safetyfail",
        "visit_summary는 진행 + 맨 위 safety.unavailable 경고 줄 · 알레르기 줄 없음",
        judge=visit_timeline(),
    ),
    _case(
        "T24-norecord",
        V,
        "C48-norecord",
        "visit.no_record(30일 · 복약 코스 없음) · 모델 호출 안 함",
        judge=visit_no_record(),
    ),
    # ── hook — `check_symptom_repetition` 을 직접 부른다 (pipeline 연결은 다른 이슈) ──
    _case(
        "T25",
        None,
        "C48",
        "기록된 날 수로 셈 · 최근 7일 안 3일이면 symptom.repeat 글자 그대로 + 병원 찾기 버튼",
        judge=symptom_repeat(symptom="발열", condition=None),
    ),
    _case(
        "T25-chronic",
        None,
        "C48-chronic",
        "symptom.repeat_condition 글자 그대로('만성 면역력 저하증' 원문) + condition_guidance "
        "카드에 출처 · health_safety 변경 0건",
        judge=symptom_repeat(symptom="기침", condition="만성 면역력 저하증"),
    ),
    _case(
        "T25-chronic-unknown",
        None,
        "C48-chronic-unknown",
        "symptom.repeat 기본 문장만 · 안내 카드 없음",
        judge=symptom_repeat(symptom="기침", condition=None),
    ),
    _case(
        "T25-allergyonly",
        None,
        "C48-allergyonly",
        "symptom.repeat 기본 문장만 · 안내 카드 없음",
        judge=symptom_repeat(symptom="기침", condition=None),
    ),
    _case(
        "T25-safetyfail",
        None,
        "C48-safetyfail",
        "symptom.repeat 기본 문장만",
        judge=symptom_repeat(symptom="기침", condition=None),
    ),
    # ── 검진 · 접종 ──
    _case("T30", S, "C48", "현재·다음 차수 (48개월: 6–7차 구간)", judge=schedule_check(6, 7)),
    _case("T30-C2", S, "C2", "2차(4–6개월) 안내", judge=schedule_check(None, 2)),
    _case("T30-C23", S, "C23", "4차(18–24개월) 구간", judge=schedule_check(4, 5)),
    _case("T30-C24", S, "C24", "다음 차수 5차(30–36개월) 안내", judge=schedule_check(4, 5)),
    _case("T30-C71", S, "C71", "8차(66–71개월) 구간", judge=schedule_check(8, None)),
    _case(
        "T30-C72",
        S,
        "C72",
        "checkup.aged_out 글자 그대로 · 이어서 이 시기 접종 목록(만 4–6세 · 만 6세)",
        judge=schedule_check(None, None, aged_out=True, due=True),
    ),
    _closed("T30-noconsent", S),
    _case(
        "T31",
        S,
        "C48",
        "이 시기 권장 접종 + vaccine.disclaimer",
        judge=schedule_check(6, 7),
    ),
    _case("T31-C35", S, "C35", "추가 접종 구간", judge=schedule_check(5, 6, due=True)),
    _case(
        "T31-C36",
        S,
        "C36",
        "vaccine.none_now + 다음 접종 시기(만 4–6세 추가 접종)",
        judge=schedule_check(5, 6, due=False, upcoming=True),
    ),
    _case(
        "T31-C71",
        S,
        "C71",
        "권장 접종 목록 + vaccine.disclaimer",
        judge=schedule_check(8, None, due=True),
    ),
    _case(
        "T31-C72",
        S,
        "C72",
        "checkup.aged_out · 이 시기 접종 목록(DTaP 5차 · IPV 4차 · MMR 2차 · 일본뇌염 4차) "
        "+ vaccine.disclaimer",
        judge=schedule_check(None, None, aged_out=True, due=True),
    ),
    _case("T32", S, "C48", "권장 목록 + 예방접종도우미 안내", judge=schedule_check(6, 7)),
    _case(
        "T33",
        S,
        "C48",
        "event_requests 1건이 초안 payload로 나감 · event 테이블 0건",
        judge=schedule_check(6, 7, alarm=True),
    ),
    _case(
        "T34",
        S,
        "C48",
        "checkup_context · 식사 경향·복약·최근 증상·알레르기·만성질환 · 고정 footer",
        judge=checkup_context(),
    ),
    _case(
        "T35",
        S,
        "C48",
        "대필 거절 + 요약 제공",
        judge=checkup_context(ghostwrite=True),
    ),
    # ── 병원 찾기 ──
    _case("T41", P, "C48", "평일 주간 → 소아청소년과 거리순 ≤5"),
    _case("T41-nolocation", P, "C48-nolocation", "위치 허용 안내"),
    _case("T41-apifail", P, "C48-apifail", "place.unavailable 글자 그대로"),
    _closed("T41-noconsent", P),
    _case("T42", P, "C48", "달빛어린이병원 → 응급의료기관", night=True),
]
