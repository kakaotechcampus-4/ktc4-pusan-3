"""LLM 을 부르는 파일은 사용량을 기록해야 한다 (BM 산정 — 보호자별 토큰이 새지 않게).

새 LLM 호출 지점이 `llm_usage.record(` 없이 들어오면 이 테스트가 실패한다. 호출 지점을 찾는 눈이
먼저 믿을 만한지(#179 의 교훈) — 일부러 기록을 빼 먹은 파일을 잡는지, 지금 아는 호출 지점을 실제로
보는지 — 를 같이 확인한다.

새 호출 지점을 만들었다면: 응답의 usage 를 읽어 `llm_usage.record(...)` 를 부르고, 그 지점을 부르는
창구에서 `llm_usage.attribute(...)` 를 건다 (docs/ops/llm-usage-v1.md).
"""

import re
from pathlib import Path
from typing import get_args

from app.core.llm_usage import Source

APP = Path(__file__).resolve().parents[3] / "app"

# LLM 을 부르거나 부를 클라이언트를 만드는 흔적. 주석 · 독스트링 말고 코드에 있는 모양만 센다.
_CALL_MARKERS = re.compile(
    r"""
    \.(?:completions|embeddings|responses)\.create\(   # openai SDK 호출
    | \b(?:Async)?OpenAI\(                              # openai 클라이언트 생성
    | ["']/decisions["']                                # Curator 판정 (httpx 직접 호출)
    """,
    re.VERBOSE,
)
_RECORD = "llm_usage.record("

# 이 파일들은 지금 LLM 을 부른다 — 위 정규식이 이 파일들을 못 보면 검사 도구가 눈이 먼 것이다
KNOWN_CALL_SITES = {
    "agents/common/llm_client.py",
    "agents/curator/embedding/embedder.py",
    "agents/curator/embedding/jev.py",
    "providers/meal_plan/image_ocr.py",
}


def _code_lines(text: str) -> str:
    """주석 줄은 뺀다. 독스트링은 못 걸러내므로 마커는 코드에서만 쓰는 모양으로 골랐다."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def call_sites(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if _CALL_MARKERS.search(_code_lines(path.read_text(encoding="utf-8")))
    }


def unrecorded(root: Path) -> set[str]:
    return {
        site
        for site in call_sites(root)
        if _RECORD not in (root / site).read_text(encoding="utf-8")
    }


def test_검사_도구가_기록을_빼_먹은_파일을_잡는다(tmp_path: Path) -> None:
    (tmp_path / "forgot.py").write_text(
        "async def ask(client):\n    return await client.chat.completions.create(model='x')\n",
        encoding="utf-8",
    )
    (tmp_path / "records.py").write_text(
        "from app.core import llm_usage\n"
        "async def ask(client):\n"
        "    response = await client.embeddings.create(model='x')\n"
        "    llm_usage.record(role='x', provider='p', model='m', prompt_tokens=1)\n",
        encoding="utf-8",
    )
    (tmp_path / "unrelated.py").write_text(
        "# client.chat.completions.create( 는 주석\n", encoding="utf-8"
    )

    assert unrecorded(tmp_path) == {"forgot.py"}


def test_검사_도구가_지금_아는_호출_지점을_실제로_본다() -> None:
    assert KNOWN_CALL_SITES <= call_sites(APP)


def test_LLM_을_부르는_모든_파일이_사용량을_기록한다() -> None:
    assert unrecorded(APP) == set()


# 인자 안에 `uuid4()` 같은 괄호가 있어도 지나가야 해서 `)` 를 막지 않는다.
# source 는 키워드 필수 인자라 호출마다 자기 값이 먼저 잡힌다
_ATTRIBUTE_SOURCE = re.compile(r"llm_usage\.attribute\(.*?source=\"([^\"]*)\"", re.DOTALL)


def attribute_sources(root: Path) -> dict[str, set[str]]:
    """`llm_usage.attribute(..., source="...")` 에 글자로 적힌 창구 값을 파일별로."""
    found: dict[str, set[str]] = {}
    for path in root.rglob("*.py"):
        values = set(_ATTRIBUTE_SOURCE.findall(path.read_text(encoding="utf-8")))
        if values:
            found[path.relative_to(root).as_posix()] = values
    return found


def test_attribute_에_적은_창구_값은_모두_Source_값이다() -> None:
    """Source 를 줄이고 옛 값을 쓰는 가지가 합쳐져도 런타임에는 조용하다 (#710 리뷰).

    그래서 코드에 적힌 값을 여기서 잡는다.
    """
    allowed = set(get_args(Source))

    bad = {
        site: values - allowed
        for site, values in attribute_sources(APP).items()
        if values - allowed
    }

    assert bad == {}


def test_창구_값을_찾는_눈이_지금_아는_귀속_지점을_본다() -> None:
    seen = set().union(*attribute_sources(APP).values())

    assert seen == set(get_args(Source))


def test_창구_값_검사가_옛_값을_잡는다(tmp_path: Path) -> None:
    (tmp_path / "old.py").write_text(
        'with llm_usage.attribute(\n    parent_id=p, run_id=r, source="meal_ocr"\n):\n    pass\n',
        encoding="utf-8",
    )

    assert attribute_sources(tmp_path) == {"old.py": {"meal_ocr"}}
