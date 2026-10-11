"""읽기 전용 출력. 저장하지 않고 그 세션에서만 산다.

피드백·만료·승인이 없는 일회성 출력이라 `suggestion` 과 성격이 다르다.
성장 추이 서술, 검진 안내, 미지원 안내, 병원 목록이 여기 해당한다.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from app.agents.common.refs import Ref

AuthoredBy = Literal["code", "model"]

# 화면이 문구 말고 모양으로 갈라야 하는 안내 (#249 의 groups[].notice 와 같은 값).
# fewer(1~2개) · empty(0개)는 count_notice 만 붙인다. 날씨 안내처럼 같은 kind="notice" 라도
# 개수와 상관없는 안내는 None 이다.
# TODO(#249): cannot_resume(이어서 할 수 없음)은 05 추천 엔드포인트가 다시 시도를 가를 때 붙인다.
#   그때 여기에 값을 더하고, 문구는 suggestion.readout.yaml 에 두어 entrypoint 로 내보낸다.
#   agent_result(#227)는 이 값을 쓰지 않는다 — run 안에서는 이어서 할 일이 없다
NoticeCode = Literal["fewer", "empty"]


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
    code: NoticeCode | None = None

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
            "code": self.code,
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

    @classmethod
    def from_yaml(cls, path: Path) -> "ReadoutCatalog":
        """`*.readout.yaml` 을 읽는다. 최상위 키가 readout 키이고 값에 `template` · `kind` 가 있다.

        파일이 없거나 모양이 틀리면 import 때 바로 실패한다 — 문구가 빈 채로 서버가 뜨지 않게.
        """
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or not data:
            raise ValueError(f"{path.name} 에 문구가 없다")
        texts: dict[str, ReadoutText] = {}
        for key, entry in data.items():
            if not isinstance(entry, dict) or set(entry) != {"template", "kind"}:
                raise ValueError(f"{path.name} 의 {key!r} 는 template · kind 두 칸이어야 한다")
            template = entry["template"]
            if not isinstance(template, str) or not template.strip():
                raise ValueError(f"{path.name} 의 {key!r} 문구가 비었다")
            texts[key] = ReadoutText(key=key, template=template, kind=entry["kind"])
        return cls(texts=texts)

    def get(self, key: str) -> ReadoutText:
        text = self.texts.get(key)
        if text is None:
            raise KeyError(f"정의되지 않은 readout 키: {key!r}")
        return text

    def render(self, key: str, **values: Any) -> Readout:
        return self.get(key).render(**values)
