"""급식표 사진 → `MealPlanJSON` — Elice MLAPI(OpenAI 호환 게이트웨이) 로 Gemini 를 부른다.

- 게이트웨이가 `/v1/chat/completions` 만 열어 두어 `openai` SDK 로 부른다.
  엔드포인트는 모델마다 다르다.
- 지원 목록 밖 매개변수는 400 으로 거절된다 → `max_tokens` 가 아니라 `max_completion_tokens`.
- 예산을 넘기면 팀 키가 자동 삭제되므로 인스턴스마다 호출 상한을 둔다.
- 로그에는 토큰 수만 남긴다. 이미지 · 원문은 남기지 않는다 (루트 CLAUDE.md §2 개인정보).
- 모델은 `source` 를 채우지 않는다. 읽기 코드가 `image` 로 채운다 (docs/meal-plan §3).
"""

import base64
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from openai import OpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import Settings
from app.providers.meal_plan.schema import (
    MealDay,
    MealPlanJSON,
    MealPlanSource,
    UnparsedCell,
)

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.1-pro-preview"
SUPPORTED_MIME = frozenset({"image/png", "image/jpeg", "image/webp"})

# 게이트웨이가 응답을 6,000 토큰에서 자른다 (실측 2026-09-09). 한 달치 JSON 은 그보다 크므로
# 날짜 범위를 나눠 부르고 합친다. 두 번째 호출부터는 프롬프트·스키마가 캐시에서 읽혀 입력이 싸다.
DEFAULT_DAY_RANGES: tuple[tuple[int, int], ...] = ((1, 15), (16, 31))

SYSTEM_PROMPT = """\
당신은 급식표 이미지를 정해진 JSON 으로 옮기는 전사 담당자입니다.
해석하거나 정리하지 말고, 적힌 그대로 옮기는 것이 임무입니다.

## raw — 가장 중요
- 각 메뉴 칸에 적힌 글자를 숫자·쉼표·점·괄호·기호(★ & / :)까지 **그대로** raw 에 넣습니다.
  예: "모듬버섯된장국5,6" → raw "모듬버섯된장국5,6"  /  "백김치9(백깍두기9)" → 그대로
  "애호박국:⑤⑥" → 그대로 (원문자와 콜론도 raw 에 남깁니다)
- 메뉴 뒤에 붙은 숫자·원문자는 알레르기 번호입니다. **지우거나 옮기거나 해석하지 마세요.**
- allergen_codes 같은 필드를 만들지 마세요. 번호를 해석하는 것은 별도 프로그램이 합니다.
- name 은 raw 에서 번호와 기호를 뺀 메뉴 이름입니다.

## 한 칸에 메뉴가 여러 개일 때
- 쉼표로 이어진 서로 다른 음식은 항목을 나눕니다. "떡(증편),우유2" → "떡(증편)" 과 "우유2".
- "A 또는 B" 는 대체 메뉴이므로 **반드시 한 항목**입니다. raw 에 "A 또는 B" 를 통째로 적고,
  "경기도과일" 을 따로 항목으로 만들지 마세요. "또는" 이 줄바꿈 뒤에 와도 같은 항목입니다.
- 맞춤법을 고치지 마세요. "쨈" 이라 적혀 있으면 "쨈", "물만둣국" 이면 "물만둣국" 그대로입니다.
- "&" 는 기호입니다. 숫자 8 로 읽지 마세요. "5,6&잼" 은 번호 5,6 과 "잼" 입니다.

## 구조
- year_month: 제목의 연월. "2025년 1월" → "2025-01".
- day: 날짜 칸 머리의 숫자만 (요일 제외). 휴일이면 meals 를 빈 배열로 두고 note 에 이유를 적습니다.
- meal_type: 아침 → "breakfast", 오전간식 → "snack_am", 점심 → "lunch", 오후간식 → "snack_pm",
  저녁·석식 → "dinner".
- 열량/단백질, 원산지 표기, 안내사항 행은 메뉴가 아닙니다. 항목으로 넣지 마세요.
- 날짜가 없는 열(예: 생일식단)은 days 에 넣지 말고 unparsed 에 raw 와 함께 적습니다.
- institution_name: 표에 기관명이 있으면 그대로, 없으면 null.
- notes: 안내사항이 있으면 한두 문장으로, 없으면 null.

## 못 읽는 칸
- 흐리거나 잘려서 확신이 없는 칸은 **지어내지 마세요.**
  unparsed 에 day, meal_type, 읽은 만큼의 raw, why 를 적습니다.
- 숫자 하나가 5인지 6인지 확신이 없어도 unparsed 로 보냅니다.
  틀린 번호 하나가 알레르기 아동에게 위험합니다.
"""


class OcrOutput(BaseModel):
    """모델이 내는 모양. `MealPlanJSON` 에서 `source` 만 뺐다 — 출처는 모델이 정하지 않는다."""

    model_config = ConfigDict(extra="forbid")

    institution_name: str | None
    year_month: str
    days: list[MealDay]
    unparsed: list[UnparsedCell]
    notes: str | None


# strict 스키마를 직접 보낸다. SDK `.parse()` 를 안 쓰는 이유는 parse_output 참고.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "MealPlanJSON",
        "schema": to_strict_json_schema(OcrOutput),
        "strict": True,
    },
}


@dataclass(frozen=True)
class Usage:
    """토큰 수만 센다. 비용 추정은 평가 스크립트(5/5)의 몫."""

    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cached_prompt_tokens + other.cached_prompt_tokens,
        )


def build_messages(
    image_bytes: bytes, mime_type: str, day_range: tuple[int, int] | None = None
) -> list[dict]:
    """이미지를 base64 data URI 로 실어 OpenAI 호환 messages 를 만든다. 축소·재인코딩하지 않는다.

    day_range 를 주면 그 날짜만 옮기라고 지시한다 (출력 상한 대응).
    """
    if mime_type not in SUPPORTED_MIME:
        raise ValueError(f"지원하지 않는 이미지 형식: {mime_type}")
    data_uri = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    text = "이 급식표를 읽어서 정해진 JSON 형식으로 옮겨 주세요."
    if day_range is not None:
        first, last = day_range
        text += (
            f" 이번 호출에서는 {first}일부터 {last}일까지만 days 에 넣고,"
            " 그 밖의 날짜는 넣지 마세요. 안내사항·기관명은 그대로 적습니다."
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        },
    ]


def parse_output(content: str) -> OcrOutput:
    """모델이 낸 JSON 문자열 → OcrOutput.

    게이트웨이가 결과를 `{"MealPlanJSON": {...}}` 처럼 스키마 이름으로 한 겹 감싸 보내는 경우가
    있다 (실측). SDK 자동 파싱은 이 겹을 몰라 실패하므로 여기서 벗긴 뒤 검증한다.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"모델 응답이 JSON 이 아님 — 파싱 실패: {e}") from e
    if isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), dict):
        (data,) = data.values()  # OcrOutput 은 필수 키가 5개라 1개짜리는 항상 겹이다
    try:
        return OcrOutput.model_validate(data)
    except ValidationError as e:
        raise RuntimeError(f"모델 응답을 급식표로 파싱 실패: {e.errors()[:3]}") from e


def merge_outputs(parts: Sequence[OcrOutput]) -> MealPlanJSON:
    """날짜 범위별 호출 결과를 한 달치 `MealPlanJSON` 으로 합친다.

    같은 날짜가 두 번 오면 앞 호출을 남기고 뒤 것은 검수 플래그로 남긴다.
    """
    first = parts[0]
    seen: set[int] = set()
    days, dupes = [], []
    for day in sorted((d for p in parts for d in p.days), key=lambda d: d.day):
        (dupes if day.day in seen else days).append(day)
        seen.add(day.day)
    unparsed = [u for p in parts for u in p.unparsed]
    unparsed += [
        UnparsedCell(day=d.day, why="호출 간 중복 날짜 — 뒤 호출 결과를 버림") for d in dupes
    ]
    notes = " / ".join(n for n in (p.notes for p in parts) if n) or None
    try:
        return MealPlanJSON(
            source=MealPlanSource.IMAGE,
            year_month=first.year_month,
            institution_name=first.institution_name,
            days=days,
            unparsed=unparsed,
            notes=notes,
        )
    except ValidationError as e:
        raise RuntimeError(f"모델 응답이 급식표 규칙에 맞지 않음: {e.errors()[:3]}") from e


class ImageOcrMealPlanReader:
    """`MealPlanReader` 구현체 — 사진. 비교용 모델은 `model` 만 바꿔 만든다."""

    source = MealPlanSource.IMAGE

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = DEFAULT_MODEL,
        reasoning_effort: str = "medium",
        max_calls: int = 10,
        timeout_s: float = 300.0,
        cache_write: str | None = None,
        day_ranges: Sequence[tuple[int, int]] | None = DEFAULT_DAY_RANGES,
    ) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=1)
        self.model = model
        self._effort = reasoning_effort
        self._max_calls = max_calls
        self._cache_write = cache_write
        self._day_ranges = tuple(day_ranges) if day_ranges else None
        self.calls = 0
        self.last_usage: Usage | None = None
        self.total_usage = Usage(0, 0, 0)

    @classmethod
    def from_settings(cls, settings: Settings, **overrides) -> "ImageOcrMealPlanReader":
        """`MEAL_OCR_*` 로 만든다. 키가 비어 있으면 사진 입력만 못 쓰는 것이지 서버는 뜬다."""
        if not settings.MEAL_OCR_BASE_URL or not settings.MEAL_OCR_API_KEY:
            raise RuntimeError(
                "MEAL_OCR_BASE_URL · MEAL_OCR_API_KEY 가 비어 있다. apps/api/.env 확인"
            )
        return cls(
            base_url=settings.MEAL_OCR_BASE_URL,
            api_key=settings.MEAL_OCR_API_KEY,
            model=settings.MEAL_OCR_MODEL,
            **overrides,
        )

    def read(self, data: bytes, *, mime_type: str) -> MealPlanJSON:
        if mime_type not in SUPPORTED_MIME:
            raise ValueError(f"지원하지 않는 이미지 형식: {mime_type}")
        ranges: tuple[tuple[int, int] | None, ...] = self._day_ranges or (None,)
        if self.calls + len(ranges) > self._max_calls:
            raise RuntimeError(
                f"호출 상한 {self._max_calls}회 — 이번 읽기에 {len(ranges)}회 필요, 시작하지 않음"
            )
        return merge_outputs([self._call(data, mime_type, r) for r in ranges])

    def _call(
        self, image_bytes: bytes, mime_type: str, day_range: tuple[int, int] | None
    ) -> OcrOutput:
        if self.calls >= self._max_calls:
            raise RuntimeError(f"호출 상한 {self._max_calls}회 도달 — 예산 보호를 위해 중단")
        self.calls += 1

        extra_body = {"cache_write": self._cache_write} if self._cache_write else None
        response = self._client.chat.completions.create(
            model=self.model,
            messages=build_messages(image_bytes, mime_type, day_range),  # type: ignore[arg-type]
            response_format=RESPONSE_FORMAT,  # type: ignore[arg-type]
            max_completion_tokens=16_000,
            reasoning_effort=self._effort,  # type: ignore[arg-type]  # MLAPI 는 xhigh·max 도 받는다
            extra_body=extra_body,
        )

        if response.usage is not None:
            self.last_usage = Usage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                cached_prompt_tokens=int(getattr(response.usage, "cached_prompt_tokens", 0) or 0),
            )
            self.total_usage = self.total_usage + self.last_usage
            log.info(
                "meal_ocr call=%d range=%s model=%s prompt=%d completion=%d cached=%d",
                self.calls,
                day_range,
                self.model,
                self.last_usage.prompt_tokens,
                self.last_usage.completion_tokens,
                self.last_usage.cached_prompt_tokens,
            )

        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError(
                "응답이 출력 상한에서 잘림 — day_ranges 를 더 잘게 나누거나 effort 를 낮출 것"
            )
        if choice.message.refusal:
            raise RuntimeError(f"모델이 응답을 거절했습니다: {choice.message.refusal}")
        if not choice.message.content:
            raise RuntimeError("모델 응답이 비어 있습니다")
        return parse_output(choice.message.content)

    def list_models(self) -> list[str]:
        """접속·키 확인용. 과금 없음."""
        return [m.id for m in self._client.models.list()]
