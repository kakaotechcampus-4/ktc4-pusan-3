from sqlalchemy import Enum as SAEnum


def enum_col(*values: str, name: str) -> SAEnum:
    return SAEnum(
        *values,
        name=name,
        native_enum=False,
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
