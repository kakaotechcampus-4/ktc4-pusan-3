# eval — AI 품질 점검

설계 · 판정 기준 · 결정 근거는 [`docs/eval/eval-v1.md`](../docs/eval/eval-v1.md). 여기는 실행법과 결과 읽는 법만 둔다.

## 무엇이 있나

| 경로 | 무엇 |
| --- | --- |
| `cases/probes.yaml` | P · 단발 프로브 31개 (능력 6개 · 헤드라인 10개 · 주입 6개) |
| `cases/week/` | W · 일주일 페르소나 — `a-typical.yaml`(보통 · 16단계) · `c-light.yaml`(가끔 · 4단계). B(많음)는 아직 없다 |
| `faults.md` | F · 장애 점검표 (계획 3) |
| `results/<날짜>.md` | 실행 요약 — 커밋한다. 지난 요약과의 diff 가 회귀 신호다 |
| `results/raw/` | run 마다의 원본 jsonl — 커밋하지 않는다 |

하네스 코드는 `apps/api/tests/eval/suite/` 에 있다.

## 실행

실제 모델을 부른다 — 월 크레딧(12만 원, 넘으면 키 삭제)을 같이 쓴다. eval 몫은 25% 이하다.

| 명령 | 내용 | 대략 입력 수 |
| --- | --- | --- |
| `make test` | 판정기 단위 테스트 (모델 안 부름) | 0 |
| `make eval-smoke` | `smoke: true` 케이스 1회 | ~15 |
| `make eval` | P 전체 3회 (W 는 안 돈다) | ~90 |
| `make eval-week` | W 일주일 시나리오 · `DB_NAME=icatch_it` 로 돈다 | ~20 |
| `make eval-report` | 가장 최근 결과 → `results/raw/<같은 이름>.md` (커밋 안 함) | 0 |

- 한 케이스만: `EVAL_ONLY=P-REC-02 make eval`
- 기준선 · 정기 실행만 커밋한다: `cd apps/api && uv run python -m tests.eval.suite.report <jsonl> --out ../../eval/results/<날짜>.md`
- 토큰 상한: `EVAL_MAX_TOKENS` (기본 3,000,000 — 입력 하나 ≈22k 토큰, 전체 한 번 ≈2.0M) 를 넘으면 남은 케이스를 건너뛰고, 건너뛴 케이스는 요약의 "실행하지 않은 케이스" 에 나온다

## W · 일주일 시나리오 실행

앱을 프로세스 안에서 API 로 돌린다(`POST /inputs` → SSE · 승인 · 정정). 시계는 `time-machine` 이 단계 시각(주의 요일 · 시:분 KST)으로 옮기고, 저장소는 **통합 테스트 DB `icatch_it`** 다(개발 DB 가 아니다). 실제 모델을 부른다 — 입력 하나 ≈22k 토큰.

```
cd apps/api
EVAL_TIER=full EVAL_ONLY=W-C PYTEST_ADDOPTS= uv run pytest tests/eval/suite/test_week_live.py -m live -q   # 가끔 (4 입력)
EVAL_TIER=full EVAL_ONLY=W-A PYTEST_ADDOPTS= uv run pytest tests/eval/suite/test_week_live.py -m live -q   # 보통 (16 단계)
```

- `make eval-week` 가 W-A · W-C 를 돈다(`EVAL_ONLY=W-C make eval-week` 로 하나만). `make eval` 은 P 만 돈다. `make eval-smoke` 는 W 를 건너뛴다(`EVAL_ONLY=W-C` 로 지정하면 돈다)
- 🚨 W 는 `DB_NAME` 이 `icatch_it` 가 아니면 시작 전에 실패한다. 실행마다 시험 보호자 · 아이 · 토큰 · 관찰 행을 `icatch_it` 에 쓰고, **케이스가 끝나면(실패해도) 제품의 삭제 경로(`purge_child` · `purge_parent`)로 지운다** — 동의 증빙(`consent_retention`)과 심은 레시피 행도 같이 지운다. 남기면 전역으로 세는 통합 테스트가 깨진다. 개발 DB 에는 닿지 않는다
- 케이스마다 1회다. 주(D) = 실행일 뒤 7일 이상 지난 첫 월요일. 실행마다 새 보호자 · 아이를 만든다
- 결과는 `results/raw/week-<tier>-<시각>.jsonl`. 단계 하나가 한 줄이고, 끝 상태 점검(`kind: check`)도 한 줄이다. `make eval-report` 가 "W · 일주일 시나리오" 절(케이스별 단계 통과 · 끝 상태 점검 · 하루 입력 한도 분석)을 더해 그린다
- W 는 하루 입력 한도를 끄고 돈다. 대신 보호자 · 날짜별 입력 수를 세어 한도 N=5 · 8 · 10 에서 막히는 수를 표로 낸다
- 단계 기대는 가볍게(라우팅 · 저장 도메인 · 추천 종류) 두고, 무거운 판정은 불변식 14개와 끝 상태 점검이 맡는다. I5(안전 정보 불변) · I8(하루치로 확정 금지)는 W 에서만 문다
- Health 는 기록만 본다 — 복약 · 병원 단계는 대본에 없다 (eval-v1 §13-6). 날씨는 `rain_days` 요일만 강한 비로 끼운다(위치 동의 · 흐린 좌표를 입력에 싣는다). 행사 · 병원 어댑터는 없다

> 🚨 **W 의 Curator 는 가짜 임베딩 · 가짜 판정기다 — 실제 묶음 품질은 보지 않는다.** 임베더는 글자마다 직교하는 벡터를 주고, 판정기는 "한쪽 이름이 다른 쪽에 들어 있으면 같은 대상" 이라는 글자 포함만 본다. 그래서 W 가 보는 것은 승격 규칙(서로 다른 날 수 · 감쇠 · 정정 반영)이고, 실제 임베딩이 블록놀이 · 블록 쌓기를 한 프로필로 묶는지는 Curator 자체 테스트의 몫이다.

## 결과 읽는 법

- **불변식 위반은 0 이어야 한다.** 1건이라도 있으면 회귀다 (루트 CLAUDE.md §2 를 어겼다)
- **헤드라인 N/10** — 노션 eval 10개를 이 스위트 안에서 다시 센 것. 목표 9/10
- **safety 케이스는 반복 전부, quality 는 3번 중 2번** 통과해야 통과다
- 통과율 옆의 구간은 Wilson 95%. 실패 0건이면 "실패율 < 3/n" 까지만 말할 수 있다
- 실패한 케이스는 사람이 transcript 를 읽고 원인을 하나로 적는다 — 프롬프트 · 규칙 코드 · 케이스 기대 · 하네스 · 모델 흔들림. eval 담당이 직접 고치지 않고 담당자에게 이슈로 넘긴다

## 케이스를 더할 때

- 문장은 전부 지어낸 것. 실제 사용자 발화 · 아이 정보 금지 (저장소가 public)
- 칸 이름이 틀리면 실행 전에 실패한다 — `make test` 로 먼저 확인
- 케이스 수 · 능력별 수 · 헤드라인 · smoke 수는 `apps/api/tests/eval/suite/test_suite_checks.py` 의 모양 테스트가 고정해 둔다 — YAML 을 고치면 그 테스트도 같이 고친다
- `make eval` 은 케이스가 하나라도 실패하면 0 이 아닌 값으로 끝난다 — 요약은 그 뒤에 `make eval-report` 를 따로 돌린다
