from sqlalchemy import Enum as SAEnum


def enum_col(*values: str, name: str) -> SAEnum:
    """값이 정해진 컬럼을 VARCHAR + CHECK 로 만든다.

    native_enum=False — PG 네이티브 ENUM 타입을 쓰지 않는다.
      네이티브는 값을 지우는 명령이 없다(`ALTER TYPE ... DROP VALUE` 가 존재하지 않음).
      잘못 넣은 값을 되돌리려면 새 타입 생성 → 컬럼 이관 → 옛 타입 삭제 3단 마이그레이션을
      손으로 써야 하고, Alembic autogenerate 가 ENUM 변경을 잡지 못한다.
      우리는 값이 계속 움직이는 단계라 CHECK 교체 한 번으로 끝나는 쪽을 택했다.

    length=32 — 32자를 쓰라는 뜻이 아니라 여유를 두는 것이다.
      이 인자를 빼면 SQLAlchemy 가 "현재 값 중 최장 길이"로 VARCHAR 를 만든다.
      engagement_level 이 varchar(4)('high') 가 되고, 나중에 'medium' 을 추가할 때
      CHECK 를 고쳐도 "value too long for type character varying(4)" 로 막힌다.
      에러가 값 검증이 아니라 길이 초과로 나서 원인을 찾기 어렵다.
      현재 최장 값은 'institution_notice'(18자)이므로 32 면 충분하고,
      VARCHAR 는 선언 길이만큼 공간을 미리 잡지 않아 낭비도 없다.
    """
    return SAEnum(
        *values,
        name=name,
        native_enum=False,
        length=32,
        create_constraint=True,
        validate_strings=True,
    )


# ---------- 공유 ENUM 정의 ----------

memory_domain = enum_col("food", "activity", "education", name="memory_domain")

profile_state = enum_col("candidate", "confirmed", "archived", name="profile_state")

confidence_source = enum_col(
    "institution_notice",
    "parent_direct",
    "parent_hedged",
    "parent_hearsay",
    name="confidence_source",
)

observation_status = enum_col("active", "inactive", name="observation_status")

engagement_level = enum_col("low", "mid", "high", name="engagement_level")

# ---------- strong_signals 허용값 ----------
# generalization 은 유효 월령 72+ 라 1차 배포 타겟(≤71개월)에서 제외 확정
STRONG_SIGNALS = (
    "resistance_to_redirect",
    "self_initiated",
    "comparative_choice",
    "asks_questions",
    "role_extension",
)
