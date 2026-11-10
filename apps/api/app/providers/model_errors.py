"""모델 호출이 실패했을 때 올라오는 예외 종류.

SDK 는 `app/providers` 안에서만 import 한다 (apps/api/CLAUDE.md). 부르는 쪽(`app/agents`)이
SDK 예외를 직접 알지 않아도 "모델을 못 불렀다" 를 한 번에 잡게 여기 모은다.
"""

from openai import OpenAIError

# RuntimeError — 응답이 출력 상한에서 잘림 · 비어 있음 · 계약에 안 맞음. providers 가 직접 올린다
# OpenAIError — 시간 초과 · 요청 과다(429) · 인증 · 연결 실패. SDK 가 올린다
#   🚨 둘은 상속 관계가 아니다. RuntimeError 만 잡으면 SDK 쪽 실패가 그대로 올라가
#      처리 안 된 예외(internal_error)가 된다
MODEL_CALL_FAILURES: tuple[type[Exception], ...] = (RuntimeError, OpenAIError)
