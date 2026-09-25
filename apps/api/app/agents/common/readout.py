"""읽기 전용 출력. 저장하지 않고 그 세션에서만 산다.

피드백·만료·승인이 없는 일회성 출력이라 `suggestion` 과 성격이 다르다.
성장 추이 서술, 검진 안내, 미지원 안내, 병원 목록이 여기 해당한다.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from app.agents.common.refs import Ref

AuthoredBy = Literal["code", "model"]


@dataclass(frozen=True)
class Readout:
    """화면에 그대로 나가는 한 덩어리.

    `authored_by="code"` 면 코드가 만든 문자열이 **그대로** 화면에 간다. 모델이 다시 쓰다가
    "또래보다 작아요" 같은 판정을 붙이는 것을 막는다. **모델 입력에도 넣지 않는다.**
    """

    kind: str  # growth_delta · symptom_timeline · place_list · unsupported …
    body: str
    title: str = ""
    authored_by: AuthoredBy = "model"
    source_refs: tuple[Ref, ...] = ()

    def __post_init__(self) -> None:
        if not self.body.strip():
            raise ValueError(f"readout 본문이 비었다: kind={self.kind}")

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "authored_by": self.authored_by,
            "source_refs": [ref.to_payload() for ref in self.source_refs],
        }


@dataclass(frozen=True)
class ReadoutText:
    """코드 상수 문구 하나. `*.readout.yaml` 의 한 줄에 대응한다.

    판정·안내·경고·미지원 문구는 전부 상수이고 테스트가 **글자 단위로** 비교한다.
    `{name}` 자리는 `format()` 이 채우되, 채울 값도 코드가 만든 것이어야 한다.
    """

    key: str  # <상태>.<주제> — unsupported.milk_meal · closed.consent
    template: str
    kind: str = "unsupported"

    def render(self, **values: Any) -> Readout:
        return Readout(kind=self.kind, body=self.template.format(**values), authored_by="code")


def code_readout(text: ReadoutText, **values: Any) -> Readout:
    """상수 문구를 readout 으로. 모델을 거치지 않는 경로는 전부 이걸 쓴다."""
    return text.render(**values)


@dataclass(frozen=True)
class ReadoutCatalog:
    """한 Agent 의 상수 문구 묶음. 키로 꺼내고 없으면 즉시 실패한다."""

    texts: dict[str, ReadoutText] = field(default_factory=dict)

    def get(self, key: str) -> ReadoutText:
        text = self.texts.get(key)
        if text is None:
            raise KeyError(f"정의되지 않은 readout 키: {key!r}")
        return text

    def render(self, key: str, **values: Any) -> Readout:
        return self.get(key).render(**values)
