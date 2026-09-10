import enum

from app.infra.db.types import enum_col_py

# ---------- Python enum 정의 ----------

class MemoryDomain(str, enum.Enum):
    FOOD = "food"
    ACTIVITY = "activity"
    EDUCATION = "education"


class ProfileState(str, enum.Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    ARCHIVED = "archived"


class ConfidenceSource(str, enum.Enum):
    INSTITUTION_NOTICE = "institution_notice"
    PARENT_DIRECT = "parent_direct"
    PARENT_HEDGED = "parent_hedged"
    PARENT_HEARSAY = "parent_hearsay"


class ObservationStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class EngagementLevel(str, enum.Enum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


# ---------- 공유 SAEnum 컬럼 객체 (기존 import 호환) ----------

memory_domain = enum_col_py(MemoryDomain, name="memory_domain")
profile_state = enum_col_py(ProfileState, name="profile_state")
confidence_source = enum_col_py(ConfidenceSource, name="confidence_source")
observation_status = enum_col_py(ObservationStatus, name="observation_status")
engagement_level = enum_col_py(EngagementLevel, name="engagement_level")

# ---------- strong_signals 허용값 ----------
# generalization 은 유효 월령 72+ 라 1차 배포 타겟(≤71개월)에서 제외 확정
STRONG_SIGNALS = (
    "resistance_to_redirect",
    "self_initiated",
    "comparative_choice",
    "asks_questions",
    "role_extension",
)
