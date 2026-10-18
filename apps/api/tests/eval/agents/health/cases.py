"""Health 라이브 eval 의 케이스 표.

입력 문장은 `docs/agents/health/test_input_health.txt` 의 `[Txx]` 가 정본이고, 기대는
`test_expected_health.md` 가 정본이다. 문서에 입력이 더해졌는데 이 표를 안 고치면
`test_health_eval_cases.py` 가 깨진다.

판정은 동의 철회 다섯만 `consent_closed` 다 — 지금 시점에 완성된 경로가 그것뿐이다. 나머지는
`pending` 이라 라벨이 구현되기 전에는 GAP, 구현된 뒤에는 판정을 쓰라고 실패한다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.agents.health.schemas.task import HealthTaskType
from tests.eval.agents.health.judges import Ran, Verdict, consent_closed, pending

HEALTH_DOCS = Path(__file__).resolve().parents[6] / "docs" / "agents" / "health"

V = HealthTaskType.VISIT_SUMMARY
S = HealthTaskType.SCHEDULE_CHECK
P = HealthTaskType.PLACE_LOOKUP
M = HealthTaskType.MEDICATION
H = HealthTaskType.CARE_HANDOFF


@dataclass(frozen=True)
class Case:
    case_id: str  # "T13" · "T24-noconsent"
    input_id: str  # "T13"
    label: HealthTaskType | None  # None = hook 케이스
    world: str
    expect: str  # test_expected_health.md 의 "기대" 한 줄
    judge: Callable[[Ran], Verdict]
    night: bool = False


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
) -> Case:
    return Case(
        case_id=case_id,
        input_id=case_id.split("-")[0],
        label=label,
        world=world,
        expect=expect,
        judge=judge or pending(label.value if label else "hook"),
        night=night,
    )


def _closed(case_id: str, label: HealthTaskType, detail: str = "") -> Case:
    expect = f"{_CONSENT} · {detail}" if detail else _CONSENT
    return _case(case_id, label, "C48-noconsent", expect, judge=consent_closed)


CASES: list[Case] = [
    # ── 복약 ──
    _case(
        "T01",
        M,
        "C48",
        '초안 1장 (op="create") · dose 3 · notice_times=["08:30","12:30","18:30"] · '
        'title="항생제" · dosage="5ml" · indication_text=null · missing=[] · 저장 0건',
    ),
    _case("T02", M, "C48", '초안 0장 · "식사 전후 언제 먹이면 되나요?"'),
    _case("T03", M, "C48", 'fixed 15:00 · notice_times=["15:00"]'),
    _case("T04", M, "C48", 'breakfast −30 → notice_times=["07:30"]'),
    _case(
        "T05",
        M,
        "C48",
        'Memory 관찰 저장 먼저 · 초안의 indication_text에 "중이염" 원문 span · ends_on=+5일 · '
        "symptom_codes는 코드 산출",
    ),
    _case("T07", M, "C48", "medication_dose_log 1행 · taken_at = now · dose.logged"),
    _case("T07-2med", M, "C48-2med", "어느 약인지 되묻기 1개"),
    _case("T08", M, "C48", "dose_id = 점심 회차"),
    _case("T08-lunchlogged", M, "C48-lunchlogged", "dose.duplicate 되묻기"),
    _case("T09", M, "C48", "log_dose_skipped → status='skipped' · dose.skipped"),
    _case("T10", M, "C48", "dose.status (마지막·다음 예정·오늘 n/m회)"),
    _case("T11", M, "C48", "dose.status만 · 반환 스키마에 can_take 없음"),
    _case(
        "T12",
        M,
        "C48",
        '초안 op="update" · schedule_id · before에 기존 18:30 · 저녁 회차 fixed 19:00 · '
        "새 notice_times · 제출 전 기존 코스 그대로",
    ),
    _case("T13", M, "C48", "med.stop_confirm 되묻기 · 그 run 변경 0건 · 확인 모달 · 초안 없음"),
    _case("T14", M, "C48", "med.stop_confirm 되묻기"),
    _case("T14-2med", M, "C48-2med", "어느 약인지 먼저 되묻기 (변경 0건)"),
    _case(
        "T15",
        M,
        "C48",
        "med.stop_confirm(\"먹인 기록은 그대로 남아요\") / '네' 다음 status='stopped'",
    ),
    _case("T16", M, "C48", "용량 판단 없음 · 처방받은 곳 상담 안내"),
    _case(
        "T21",
        M,
        "C48",
        "해열제가 등록 복약이면 dose_log(taken_at 03:00) · 아니면 관찰(action_taken)",
    ),
    _closed("T01-noconsent", M, "초안 0장"),
    # ── 전달 서류 ──
    _case(
        "T18",
        H,
        "C48",
        '고정 서식 · 약·시각·보관·이유(있으면)·특이사항 · "보호자 확인 후 서명해 주세요"',
    ),
    _case("T18-nomed", H, "C48-nomed", "notice.no_active_med"),
    _closed("T18-noconsent", H),
    _case("T19", H, "C48", "응급 카드 — 알레르기·만성질환·복용 약 원문 · 연락처 줄 없음"),
    _case(
        "T19-safetyfail", H, "C48-safetyfail", "응급 카드의 알레르기 칸을 비우고 조회 실패를 명시"
    ),
    # ── 열 · 증상 · 진료 준비 ──
    _case(
        "T22",
        V,
        "C48-fever",
        "fever_timeline — 최고·최저 체온과 시각 · 마지막 복약(원문) · 등록 일정상 다음 예정",
    ),
    _case("T22-notemp", V, "C48", "fever.no_record"),
    _case("T22-C2", V, "C2", "응급 안내 먼저 (3개월 미만 발열)"),
    _case("T23", V, "C48", '에피소드 단위 요약 ("9/18–9/20 발열 3일")'),
    _case("T24", V, "C48", "symptom_timeline · 줄마다 ref · 증상 14일 · 식사 3일 · 복약 원문"),
    _closed("T24-noconsent", V),
    _case(
        "T24-safetyfail", V, "C48-safetyfail", "visit_summary는 진행 + safety.unavailable 경고 줄"
    ),
    _case("T24-norecord", V, "C48-norecord", "visit.no_record · 모델 호출 안 함"),
    # ── hook (Health-4 가 hook 함수를 붙인다) ──
    _case(
        "T25",
        None,
        "C48",
        "기록된 날 수로 셈 · 최근 7일 안 3일이면 symptom.repeat 글자 그대로 + 병원 찾기 버튼",
    ),
    _case(
        "T25-chronic",
        None,
        "C48-chronic",
        "symptom.repeat_condition 글자 그대로('만성 면역력 저하증' 원문) + condition_guidance "
        "카드에 출처 · health_safety 변경 0건",
    ),
    _case(
        "T25-chronic-unknown",
        None,
        "C48-chronic-unknown",
        "symptom.repeat 기본 문장만 · 안내 카드 없음",
    ),
    _case(
        "T25-allergyonly", None, "C48-allergyonly", "symptom.repeat 기본 문장만 · 안내 카드 없음"
    ),
    _case("T25-safetyfail", None, "C48-safetyfail", "symptom.repeat 기본 문장만"),
    # ── 검진 · 접종 ──
    _case("T30", S, "C48", "현재·다음 차수 (48개월: 6–7차 구간)"),
    _case("T30-C2", S, "C2", "2차(4–6개월) 안내"),
    _case("T30-C23", S, "C23", "4차(18–24개월) 구간"),
    _case("T30-C24", S, "C24", "다음 차수 5차(30–36개월) 안내"),
    _case("T30-C71", S, "C71", "8차(66–71개월) 구간"),
    _case("T30-C72", S, "C72", "checkup.aged_out 글자 그대로"),
    _closed("T30-noconsent", S),
    _case("T31", S, "C48", "이 시기 권장 접종 + vaccine.disclaimer"),
    _case("T31-C35", S, "C35", "추가 접종 구간"),
    _case("T31-C36", S, "C36", "만 4–6세 추가 접종 구간"),
    _case("T31-C71", S, "C71", "권장 접종 목록 + vaccine.disclaimer"),
    _case("T31-C72", S, "C72", "checkup.aged_out · 접종 목록 없음"),
    _case("T32", S, "C48", "권장 목록 + 예방접종도우미 안내"),
    _case("T33", S, "C48", "event_requests 1건이 초안 payload로 나감 · event 테이블 0건"),
    _case(
        "T34",
        S,
        "C48",
        "checkup_context · 식사 경향·복약·최근 증상·알레르기·만성질환 · 고정 footer",
    ),
    _case("T35", S, "C48", "대필 거절 + 요약 제공"),
    # ── 병원 찾기 ──
    _case("T41", P, "C48", "평일 주간 → 소아청소년과 거리순 ≤5"),
    _case("T41-nolocation", P, "C48-nolocation", "위치 허용 안내"),
    _case("T41-apifail", P, "C48-apifail", "place.unavailable 글자 그대로"),
    _closed("T41-noconsent", P),
    _case("T42", P, "C48", "달빛어린이병원 → 응급의료기관", night=True),
]
