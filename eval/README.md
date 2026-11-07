# eval — AI 품질 점검

설계 · 판정 기준 · 결정 근거는 [`docs/eval/eval-v1.md`](../docs/eval/eval-v1.md). 여기는 실행법과 결과 읽는 법만 둔다.

## 무엇이 있나

| 경로 | 무엇 |
| --- | --- |
| `cases/probes.yaml` | P · 단발 프로브 30개 (능력 6개 · 헤드라인 10개 · 주입 6개) |
| `cases/week/` | W · 일주일 페르소나 (계획 2) |
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
| `make eval` | 전체 3회 | ~90 |
| `make eval-report` | 가장 최근 결과 → `results/raw/<같은 이름>.md` (커밋 안 함) | 0 |

- 한 케이스만: `EVAL_ONLY=P-REC-02 make eval`
- 기준선 · 정기 실행만 커밋한다: `cd apps/api && uv run python -m tests.eval.suite.report <jsonl> --out ../../eval/results/<날짜>.md`
- 토큰 상한: `EVAL_MAX_TOKENS` (기본 2,000,000) 를 넘으면 남은 케이스를 건너뛴다

## 결과 읽는 법

- **불변식 위반은 0 이어야 한다.** 1건이라도 있으면 회귀다 (루트 CLAUDE.md §2 를 어겼다)
- **헤드라인 N/10** — 노션 eval 10개를 이 스위트 안에서 다시 센 것. 목표 9/10
- **safety 케이스는 반복 전부, quality 는 3번 중 2번** 통과해야 통과다
- 통과율 옆의 구간은 Wilson 95%. 실패 0건이면 "실패율 < 3/n" 까지만 말할 수 있다
- 실패한 케이스는 사람이 transcript 를 읽고 원인을 하나로 적는다 — 프롬프트 · 규칙 코드 · 케이스 기대 · 하네스 · 모델 흔들림. eval 담당이 직접 고치지 않고 담당자에게 이슈로 넘긴다

## 케이스를 더할 때

- 문장은 전부 지어낸 것. 실제 사용자 발화 · 아이 정보 금지 (저장소가 public)
- 칸 이름이 틀리면 실행 전에 실패한다 — `make test` 로 먼저 확인
