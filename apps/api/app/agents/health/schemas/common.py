"""Health tool 스키마가 같이 쓰는 base."""

from pydantic import BaseModel, ConfigDict


class ToolArgs(BaseModel):
    """모든 Health tool argument 의 base. 정의되지 않은 필드는 받지 않는다."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)
