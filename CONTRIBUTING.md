# 육아 Agent 협업 가이드

백엔드 · 프론트엔드 · AI 파트가 함께 지키는 최소 협업 규칙입니다. 자세한 운영/보안/아키텍처 설명은 `README.md`와 `README_COLLABORATION.md`를 참고합니다.

## 1. 기본 작업 흐름

1. 이슈를 먼저 만든다.
2. 이슈 번호 기준으로 브랜치를 만든다.
3. 작은 단위로 커밋한다.
4. 테스트를 통과시킨다.
5. PR을 만들고 리뷰를 받는다.
6. 승인 후 `develop`에 머지한다.

`develop` 브랜치에 직접 push하지 않습니다.

## 2. 브랜치 컨벤션

형식:

```text
<type>/<part>-<issue-number>-<short-description>
```

part — 작업하는 파트를 하나 고릅니다.

```text
be   백엔드 (apps/api 의 app/api · domains · infra · rules)
fe   프론트엔드 · 모바일 셸 (apps/web · apps/mobile)
ai   Agent · 프롬프트 (apps/api 의 app/agents · providers)
```

한 브랜치는 한 파트만 담습니다. 파트 경계를 넘는 변경(예: API 계약 변경 + 화면 반영)은 브랜치를 나누고 PR도 따로 올립니다.

type:

```text
feat      기능 추가
fix       버그 수정
refactor  구조 개선
test      테스트 추가/수정
docs      문서 수정
chore     설정/의존성/빌드 작업
hotfix    운영 긴급 수정
```

예시:

```text
feat/be-12-daily-routine-recommend
fix/be-18-quota-rollback
feat/fe-14-suggestion-approval-sheet
refactor/fe-22-split-api-client
feat/ai-19-memory-curator-promotion
docs/ai-31-supervisor-prompt-contract
```

## 3. 커밋 컨벤션

Conventional Commits 형식을 사용합니다.

```text
<type>(<scope>): <summary>
```

예시:

```text
feat(routine): add daily care routine recommendation
fix(agent): stop tool call retry loop on timeout
refactor(agent): split prompt builder from llm client
test(quota): cover daily usage limit rollback
docs(readme): document required env variables
```

규칙:

- 한 커밋은 하나의 논리적 변경만 포함합니다.
- 로직 변경과 단순 포맷팅을 섞지 않습니다.
- `.env`, API Key, 서비스 계정 자격증명, Secret 파일은 커밋하지 않습니다.

## 4. 이슈 작성 규칙

이슈 제목:

```text
[<PART> <Type>] <작업 요약>
```

`<PART>` 는 브랜치의 part 를 대문자로 씁니다 — `BE` · `FE` · `AI`.

예시:

```text
[BE Refactor] Agent service를 기능별로 분리
[BE Fix] 사용량 차감 rollback 누락 수정
[FE Feat] 추천 승인 시트 화면 추가
[FE Docs] API 클라이언트 사용 규칙 정리
[AI Feat] Curator 반복 승격 규칙 구현
[AI Docs] Supervisor 프롬프트 계약 문서화
```

이슈에는 최소한 아래 내용을 작성합니다.

- 목적
- 현재 문제
- 작업 범위
- 완료 기준
- 검증 방법

템플릿 위치:

```text
.github/ISSUE_TEMPLATE/backend-task.md
```

## 5. PR 작성 규칙

PR 제목:

```text
[<PART>] <type>(<scope>): <summary>
```

예시:

```text
[BE] refactor(agent): split agent service modules
[BE] fix(quota): prevent double usage deduction
[FE] feat(suggestion): add approval bottom sheet
[FE] chore(web): pin tanstack query version
[AI] feat(curator): promote repeated observations
[AI] docs(supervisor): document intent routing contract
```

PR에는 최소한 아래 내용을 작성합니다.

- 작업 요약
- 변경 이유
- API 계약 변경 여부
- 데이터 스키마/환경변수 변경 여부
- 테스트 결과
- 리뷰 포인트

템플릿 위치:

```text
.github/pull_request_template.md
```

## 6. PR 크기 기준

권장:

- 하나의 PR은 하나의 목적만 가진다.
- 기능 변경과 대형 리팩토링을 섞지 않는다.
- API 계약 변경은 문서 수정과 함께 올린다.
- 데이터 스키마, 사용량 제한, Agent workflow 분기 로직 변경은 테스트를 포함한다.

큰 작업은 아래처럼 나눕니다.

```text
1. 파일 이동/구조 분리
2. 내부 import 정리
3. 로직 이동
4. 테스트 보강
5. 문서 업데이트
```

## 7. 테스트 규칙

프로젝트에 정의된 테스트 명령어로 검증합니다.

- 단위 테스트: 변경한 모듈 범위
- 트랜잭션/동시성 관련 변경: 실제 DB 또는 로컬 에뮬레이터 환경에서 통합 테스트
- Agent workflow 변경: 모델 호출을 mock으로 대체한 회귀 테스트

PR에는 실행한 테스트 명령어와 결과를 적습니다.

## 8. 리뷰 기준

리뷰에서 우선 확인할 내용:

- API request/response 계약이 깨지지 않았는가
- 사용량 제한(quota)과 중복 요청 방지(idempotency) 흐름이 안전한가
- 실패 시 차감된 사용량이나 변경된 상태가 rollback 되는가
- 민감정보가 로그, 코드, 에러 응답에 남지 않는가
- 테스트가 실패 케이스를 포함하는가
- README 또는 문서 수정이 필요한 변경인가

## 9. 금지 사항

- `main, develop` 직접 push
- `.env`, secret, 서비스 계정 키 파일 커밋
- 모델 API Key를 클라이언트나 Git에 포함
- 검증 없이 사용량 또는 권한 부여
- error code 임의 변경
- 클라이언트와 합의 없이 API 응답 필드 삭제/타입 변경
- 아동·보호자 개인정보(이름, 생년월일, 건강 기록 등)를 로그나 외부 요청에 원문 노출
