"""가상 데이터만 쓰는 Jev 연결 확인. 운영 코드/DB/holdout은 사용하지 않는다.

실행 (apps/api 에서): uv run python -m tests.eval.agents.curator.jev_smoke --live
키: 환경변수 OPENROUTER_API_KEY (출력하지 않음). apps/api/.env 에 두면 서버 설정이 거부한다.
--live 없이는 요청 내용만 확인한다. 실호출은 최대 1회, 자동 재시도 없음.
"""

import argparse
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

API_ENV = Path(__file__).resolve().parents[4] / ".env"  # apps/api/.env

CASES = {
    "synonym": ("달걀", ["계란", "메추리알", "닭고기"], "계란"),
    "different": ("장난감 기차", ["장난감 비행기", "인형"], "none"),
    # 실험용 잠정 정책: 원재료와 가공품은 다른 대상이다.
    "processed": ("딸기잼", ["딸기", "블루베리"], "none"),
    "exact": ("레고", ["레고", "농구"], "레고"),
    "unclear": ("그거", ["계란", "레고"], "uncertain"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="과금 가능한 실제 요청 1회")
    args = parser.parse_args()
    questions = {}
    state = {}
    for name, (subject, candidates, _) in CASES.items():
        state[name] = {"subject": subject, "candidates": candidates}
        questions[name] = {
            "type": "choice",
            "instructions": (
                f"state의 {name}만 평가하세요. subject와 동일한 대상의 다른 표현인 "
                "후보를 선택하세요. 같은 대상에 대한 반복 관찰로 집계할 수 있어야 합니다. "
                "단순 관련성, 상위/하위 범주, 원재료/가공품은 동일하지 않습니다. "
                "일치 후보가 없으면 none, subject가 모호해 판단할 수 없으면 uncertain."
            ),
            "criteria": {
                **{candidate: f"subject와 동일한 대상: {candidate}" for candidate in candidates},
                "none": "대상은 명확하지만 동일한 후보가 없다",
                "uncertain": "정보가 부족하거나 모호하여 동일성을 판단할 수 없다",
            },
        }
    payload = {"model": "typesafe/jev-1.13", "state": state, "questions": questions}
    if not args.live:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("DRY RUN: API 호출 없음. 실제 호출은 --live")
        return 0

    key = os.environ.get("OPENROUTER_API_KEY") or dotenv_values(API_ENV).get("OPENROUTER_API_KEY")
    if not key or not key.strip():
        print("OPENROUTER_API_KEY 미설정: 환경변수 또는 apps/api/.env에 설정하세요.")
        return 2

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=45.0, follow_redirects=False) as client:
            response = client.post(
                "https://openrouter.ai/api/alpha/decisions",
                headers={"Authorization": f"Bearer {key.strip()}"},
                json=payload,
            )
    except httpx.RequestError as exc:
        print(f"통신 실패: {type(exc).__name__} (키/요청 헤더는 출력하지 않음)")
        return 2
    print(f"HTTP {response.status_code}, 왕복 {(time.perf_counter() - started) * 1000:.0f} ms")
    if response.status_code != 200:
        print("호출 실패. 401: 키, 402: 잔액, 429: 호출 제한 확인. 응답 원문은 출력하지 않음.")
        return 2
    try:
        result = response.json()
        answers = result["answers"]
        passed = 0
        for name, (_, candidates, expected) in CASES.items():
            answer = answers[name]
            choice = answer["choice"]
            if answer["type"] != "choice" or choice not in [*candidates, "none", "uncertain"]:
                raise ValueError("invalid choice")
            confidence = answer["confidence"]
            probabilities = answer["probabilities"]
            if not isinstance(probabilities, dict) or not isinstance(confidence, (int, float)):
                raise ValueError("invalid confidence/probabilities")
            passed += choice == expected
            print(f"{name}: 선택={choice}, 기대={expected}, confidence={confidence}")
            print(f"  probabilities={json.dumps(probabilities, ensure_ascii=False)}")
        print(f"일치 {passed}/{len(CASES)} (접속 확인용 소표본, 성능 결론 아님)")
        print(f"model={result.get('model')}, usage={json.dumps(result.get('usage'))}")
    except (ValueError, KeyError, TypeError):
        print("예상과 다른 응답 형식. 성공 판정하지 않음.")
        return 2
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
