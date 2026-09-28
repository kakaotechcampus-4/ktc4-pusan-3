import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, UUIDPk
from app.infra.db.types import enum_col_py


class CorrectionTargetKind(enum.StrEnum):
    """교정 대상 테이블. target_id 와 짝을 이룬다.

    observation_routine 은 테이블이 아직 없다 — Memory Agent 는 이미 routine tool 을
    갖고 있어서 테이블이 생기는 순간 교정 대상이 된다. 값을 미리 넣어 두면 그때
    correction 스키마를 다시 건드리지 않는다. FK 가 없어서 지금 넣어도 깨지지 않는다.
    """

    OBSERVATION_FOOD = "observation_food"
    OBSERVATION_HEALTH = "observation_health"
    OBSERVATION_EDUCATION = "observation_education"
    OBSERVATION_ACTIVITY = "observation_activity"
    OBSERVATION_ROUTINE = "observation_routine"
    PROFILE_AFFINITY = "profile_affinity"


class CorrectionVerdict(enum.StrEnum):
    """보호자 판정. 대상에 따라 쓸 수 있는 값이 다르다 (아래 CHECK 참고).

    "맞아요" 에 해당하는 confirm 은 없다. 동의는 아무것도 바꾸지 않으므로 이력으로도
    남기지 않는다 — 값이 있으면 저장 경로가 생기고, 그 순간 "맞아요가 무엇을 바꾸는가"
    라는 질문이 되살아난다.

    관찰에 outdated 를 두지 않는 것도 같은 이유다. 그날 그랬다는 사실은 시간이 지나도
    사실이다. 낡는 것은 그 관찰들이 만든 성향(Profile) 쪽이다.
    """

    ONCE_ONLY = "once_only"  # 관찰 — 이번만 그랬어요
    WRONG = "wrong"  # 공통 — 잘못된 기록
    NEED_MORE_OBSERVATION = "need_more_observation"  # 프로필 — 기록이 더 필요해요
    OUTDATED = "outdated"  # 프로필 — 지금은 달라요


OBSERVATION_KINDS = frozenset(
    kind for kind in CorrectionTargetKind if kind.value.startswith("observation_")
)
OBSERVATION_VERDICTS = frozenset({CorrectionVerdict.ONCE_ONLY, CorrectionVerdict.WRONG})
PROFILE_VERDICTS = frozenset(
    {
        CorrectionVerdict.NEED_MORE_OBSERVATION,
        CorrectionVerdict.OUTDATED,
        CorrectionVerdict.WRONG,
    }
)


def _in(values: frozenset[enum.StrEnum]) -> str:
    return ", ".join(f"'{v.value}'" for v in sorted(values, key=lambda v: v.value))


# 대상별 허용 verdict 를 DB 가 막는다. 파이썬 집합에서 문자열을 만들어서, 위 목록을
# 고치면 제약도 같이 움직인다 — 두 벌로 적으면 한쪽만 늘어난 날 조용히 어긋난다.
TARGET_VERDICT_CHECK = (
    f"(target_kind IN ({_in(OBSERVATION_KINDS)})"
    f" AND verdict IN ({_in(OBSERVATION_VERDICTS)}))"
    f" OR (target_kind = '{CorrectionTargetKind.PROFILE_AFFINITY.value}'"
    f" AND verdict IN ({_in(PROFILE_VERDICTS)}))"
)


class Correction(Base, UUIDPk):
    """append-only. 관찰·프로필에 대한 보호자 판정 이력.

    대상은 target_kind + target_id 로 가리킨다. 대상 테이블이 6개라 FK 로는 컬럼이
    6개가 되고, 테이블이 늘 때마다 스키마가 바뀐다. 대신 DB 가 대상 존재를 보장하지
    못하므로 대상이 실제로 있는지와 child_id 가 같은지는 서버가 검증한다 (후속 이슈).
    child_id 를 들고 있는 이유가 여기 있다 — 이게 없으면 검증도 아이별 이력 조회도
    6개 테이블 조인이 된다.

    Timestamps 믹스인을 쓰지 않는다 — 수정되지 않는 테이블이다.
    되돌리기도 UPDATE 가 아니라 새 행으로 쌓는다.
    """

    __tablename__ = "correction"
    __table_args__ = (
        CheckConstraint(TARGET_VERDICT_CHECK, name="correction_target_verdict"),
        # 계약서 §07 의 이력 조회 (GET /corrections?target_ref.kind=&target_ref.id=)
        Index("ix_correction_target", "target_kind", "target_id"),
        # 아이 삭제 시 CASCADE 가 correction 전체를 훑지 않게 한다
        Index("ix_correction_child_id", "child_id"),
    )

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    target_kind: Mapped[CorrectionTargetKind] = mapped_column(
        enum_col_py(CorrectionTargetKind, name="correction_target_kind"),
        nullable=False,
    )
    # FK 가 아니다. 가리키는 테이블이 target_kind 에 따라 달라진다
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    verdict: Mapped[CorrectionVerdict] = mapped_column(
        enum_col_py(CorrectionVerdict, name="correction_verdict"),
        nullable=False,
    )
    # 이력·감사용이다. 공동 기록의 수정 권한 판정에는 쓰지 않는다.
    # 작성자가 탈퇴해도 이력은 남는다 — 그래서 nullable + SET NULL (e7b2a9c4f013 와 같은 규칙)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
