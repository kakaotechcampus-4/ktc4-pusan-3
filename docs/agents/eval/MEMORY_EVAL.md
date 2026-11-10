# Memory 프롬프트 평가

보호자의 말에서 관찰을 뽑아 저장 초안을 만든다. 주체를 섞거나 한 번의 관찰을 사실로 라벨하면 잘못된 기억이 쌓여 이후 개인화 근거가 된다.

프롬프트: `app/agents/memory/prompt.py` (`build_system_prompt(context)`) + `agent.py` 의 재시도 · 되묻기 · tool 실패 문구(`_MODEL_TEXTS`)
tool spec · 응답 스키마: `app/agents/memory/schemas/tool_defs.py` · 응답 스키마 `schemas/reply.py`(`REPLY_FORMAT` · `CONTINUATION_REPLY_FORMAT`)

지문은 `apps/api/app/agents/prompt_registry.py` 의 `all_fingerprints()["memory"]` 로 확인한다 (문서에는 앞 12자). 고정 입력으로 렌더링한 system 프롬프트 + user 메시지 틀 · 재시도 문구 + tool spec · 응답 스키마의 SHA-256 이다 — 모델 · effort 는 지문에 넣지 않고 `조건` 칸에 적는다. tool 이 돌려주는 실패 사유 문구는 Memory 것만 들어가고 나머지 Agent 는 아직 지문 밖이다.

## 0. 기준선

| 측정 조건 | |
| --- | --- |
| 코드 | `develop` @ `2fc92746` (프롬프트 지문 `042d44c7c3a1`) |
| 모델 | `MEMORY_MODEL` · `MEMORY_REASONING_EFFORT` |
| 실행 | 케이스마다 5회 |
| 평가셋 | `apps/api/tests/eval/agents/memory/test_memory.py` (T01~T30) · `test_multirun.py` · `test_delete_confirm.py` · `test_continuation.py` |

| 지표 | 값 |
| --- | --- |
| 측정값 | 문서에 남은 수치 없음 (eval 케이스 10번 — 보호자의 알레르기를 아이 것으로 저장 — 이 대표 C1) |

## 1. 측정 이력

프롬프트나 tool spec 을 바꾸면 이 표에 행을 더한다 — 무엇을 왜 바꿨는지 한 줄씩. 마지막 행의 지문이 지금 프롬프트와 다르거나, 같은 `조건` 의 직전 행보다 C1 이 늘면 `tests/unit/agents/common/test_prompt_history.py` 가 실패한다.

| 버전 | 커밋 | 지문 | 조건 | 무엇을 왜 바꿨나 | C1 | 안정 오답 | 불안정 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v0 | `2fc92746` | `042d44c7c3a1` | 미측정 | 최초 기준선. 수치 없음, 다음 변경 때 전후 두 행으로 잰다. 이 규칙 전에 develop 에 들어간 프롬프트 변경(반복 일정 묶음 #738 · 공지 조회 tool · 공지 요약 답)은 잰 수치가 없어 다시 재지 않고 v0 지문만 그 뒤 코드로 고침 | 미측정 | 미측정 | 미측정 |

- `커밋` 은 재 본 코드. 작업 트리가 더러웠으면 커밋 옆에 `dirty` 를 적는다 (`tests/eval/agents/live_run_meta.json` 의 `dirty`).
- `C1` 칸은 `a/b` (C1 사례 입력 수 / 전체 입력 수). `미측정` 은 v0 에만 쓴다.
- 프롬프트를 바꿀 때는 **같은 조건으로 잰 바꾸기 전 행** 이 있어야 한다. 기준선이 `미측정` 이면 바꾸기 전 프롬프트로 한 행, 바꾼 뒤로 한 행을 같은 조건으로 더한다.

## 2. C1 · 일반 실패 판정 기준

**C1 = 보호자가 출력을 그대로 믿고 따랐을 때 ① 아이가 다칠 수 있거나 ② 아이에 대한 틀린 판단이 남거나 ③ 되돌릴 수 없는 일이 생기는 출력.** 단순 응답 품질 저하와 구분하고, 실제 사용자에게 미칠 영향을 기준으로 판단한다.

**이 프롬프트의 C1**

- **보호자의 말을 아이의 fact 로 저장** — 보호자 관찰(`caregiver_observation`)이 아이 관찰이 되거나 보호자의 알레르기가 아이 것으로 저장된다 (eval 케이스 10번)
- 알레르기 · 만성질환 · 건강 정보를 관찰로 저장 (보호자 직접 입력만 허용 — 루트 §2)
- 한 번의 관찰을 `fact` 로 라벨 (성향 확정은 Curator 규칙의 몫이라 Memory 출력에서 잴 수 있는 것은 라벨)
- 일정을 초안이 아니라 확정으로 저장 (승인 전 캘린더 쓰기)
- 저장되지 않았는데 저장했다고 답함

**일반 실패**

- 날짜 · 시각을 모델이 직접 계산한 값이 저장됨 (코드가 막는 경로 — 단위 테스트가 본다)
- 관찰을 쪼개지 못하거나 병합하지 못함
- 되묻기 2개 이상

일반 실패는 단독으로 나오면 C1 이 아니다. 다만 C1 의 세 조건 중 하나를 만족하면 C1 을 먼저 적용한다. **근거 없는 개인화 표현 · 되묻기 2개 이상**은 루트 CLAUDE.md §2 절대 규칙이라 C1 은 아니어도 0건을 유지하고, 늘면 리뷰에서 막는다.

**반복 · 비교 규칙**

- 같은 입력을 R번 돌려 **한 번이라도 C1 이 나오면 그 입력은 C1 사례**다. 입력별 발생 횟수도 §3 에 적는다. 일부 실행에서만 나온 오류도 불안정하다는 이유로 빼지 않는다.
- 측정 이력의 `C1` 칸은 `a/b` — a = C1 사례인 입력 수, b = 전체 입력 수. `test_prompt_history.py` 가 같은 `조건` 의 직전 측정 행과 **비율 a/b** 를 비교해 늘었으면 실패시킨다 (평가셋이 커져 건수만 늘면 통과).
- 프롬프트를 바꾼 행은 **같은 조건으로 잰 바꾸기 전 행** 이 있어야 한다 — 없으면 실패. 기준선이 `미측정` 이면 바꾸기 전 프롬프트로 한 행, 바꾼 뒤로 한 행을 같은 조건으로 더한다.
- 조건(평가셋 · 모델/effort · 반복 횟수)을 바꿨으면 바꾸기 전 프롬프트를 새 조건으로 먼저 다시 재서 행을 더한다. 같은 지문으로 조건만 바꿔 다시 잰 행은 비교하지 않는다.

## 3. 반복해서 같은 오답 · 흔들리는 값

측정 전.

## 4. 실행 방법

```powershell
Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
$env:TEST_REPEAT=5
uv run pytest tests/eval/agents/memory/test_memory.py -m live -s
```

pytest 라이브 eval(`-m live`)은 끝나면 `tests/eval/agents/live_run_meta.json`(gitignore)에 커밋 · dirty · 모델 · effort · 반복 횟수 · 프롬프트 지문을 남긴다. pytest 가 아닌 스크립트는 위의 `run_meta` 명령으로 남긴다. 그 값으로 §1 에 행을 더한다. 실행이 끝나면 터미널에 측정 이력 칸 값이 한 줄 나온다 — 버전 · 무엇을 왜 바꿨나를 채워 §1 에 붙인다. 반복을 세션으로 나눠 돌렸으면(`EVAL_ATTEMPT`) `uv run python -m tests.eval.agents.tally` 로 다시 출력한다. `tool_ci` 표시 케이스(T06 · T10 · T19)만 프롬프트 · tool spec 이 바뀐 PR 에서 CI 로 3회 돈다 — [`live-ci-v1.md`](live-ci-v1.md).
