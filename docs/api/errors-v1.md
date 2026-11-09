# 오류 응답 — 봉투와 400 · 422 규칙 (v1)

> **이 문서가 정하는 것** — 형식이 틀린 요청에 서버가 어떤 상태 코드로 답하는지, 그리고 Swagger(API 계약 정본)가 그걸 실제와 같게 안내하게 하는 장치. 주소마다의 고유 오류 코드(`owner_only` · `allergen_blocked` 등)는 각 주소의 Swagger 와 기능 문서가 정한다.
>
> 근거: `apps/api/app/api/errors.py` · `apps/api/app/api/openapi.py` · `apps/api/app/api/schema_base.py` · 테스트 `apps/api/tests/unit/api/test_openapi_shape.py` (#438)

## 1. 모양 — 봉투 하나

모든 JSON 오류는 같은 모양이다 (계약서 §01 · `ErrorEnvelope`).

```json
{ "error": { "code": "validation_failed", "message": "요청 형식이 올바르지 않아요", "detail": { "fields": ["content"] } } }
```

- `code` — 화면이 갈라 읽는 값. 화면은 `message` 로 분기하지 않는다
- `message` — 사용자에게 그대로 보여도 되는 문장. 서버가 만든다
- `detail` — 있을 때만. 형식 오류면 `fields` 에 틀린 칸 이름

예외는 302 로 답하는 로그인 두 주소뿐이다 (`auth-kakao-v1.md` §8-2).

## 2. 형식 오류는 400, 경로 값만 422

| 무엇이 틀렸나 | 상태 | `code` | 예 |
| --- | --- | --- | --- |
| **본문 · 쿼리 · 헤더** — 칸이 없거나 타입 · 길이 · 값 범위가 틀림 | **400** | `validation_failed` | `content` 가 1000자를 넘음 · 쿼리 `rated` 가 `true` · `false` 가 아님 · `Idempotency-Key` 가 비었음 |
| 본문 값을 라우터가 보고 거절 | **400** | `validation_failed` | 공백뿐인 일기 · 고칠 칸이 하나도 없는 `PATCH` |
| **경로 값** — 주소 안의 값 형식 · 범위가 틀림 | **422** | `validation_failed` | 날짜 `2026-13-40` · 정해진 종류 밖의 `provider` · uuid 가 아님 |

- pydantic 검증 실패(`RequestValidationError`)는 `errors.py` 의 `_validation_error` 가 첫 칸(`loc[0]`)으로 가른다 — `path` 면 422, 나머지는 400.
- 🚨 **경로 값이 하나라도 틀리면 본문 오류가 같이 있어도 422 다.** 한 요청의 오류를 모아 보고 경로 값 오류가 섞여 있으면 422 로 보낸다 — `detail.fields` 에는 둘 다 실린다(예: `["date", "content"]`). 400 은 경로 값이 모두 맞을 때만 온다 (#893 리뷰).
- 라우터가 직접 내는 형식 오류도 같은 규칙을 따른다. 🚨 한동안 공백뿐인 일기가 422 였다 — 본문 오류라 400 으로 맞췄다 (#438).
- 422 를 고유 코드로 쓰는 주소는 있다(예: 사진 저장의 `lane_not_supported`). 그건 형식 오류가 아니라 그 주소의 규칙이라 Swagger 에 그 주소가 직접 적는다.

## 3. Swagger 가 실제와 같게 — `app/api/openapi.py`

FastAPI 는 본문이나 경로 값을 받는 모든 주소에 자기 기본 422 모양(`HTTPValidationError`)을 붙인다. 서버는 그 모양으로 답하지 않는다. 그래서 문서를 만든 직후 한 번 고친다.

- 기본 422 → 경로 값이 있는 주소는 봉투로 바꾸고(설명 "경로 값 형식이 맞지 않아요"), 없는 주소는 지운다
- 본문 · 쿼리 · 헤더를 받는 주소에 400(봉투)이 없으면 더한다
- 라우터가 직접 적은 400 · 422 는 건드리지 않는다
- 아무도 가리키지 않게 된 `HTTPValidationError` · `ValidationError` 를 지운다

🚨 라우터마다 `responses` 에 400 · 422 를 손으로 적게 하지 않는다 — 70곳이 넘어서 하나만 빠져도 다시 어긋난다. 어긋나면 `test_openapi_shape.py` 가 잡는다.

## 4. 필드 설명 — `ApiSchema`

필드의 뜻은 스키마의 필드 바로 아래 설명 글(docstring)에 적으면 Swagger 에 나간다(루트 CLAUDE.md §5). pydantic 은 `use_attribute_docstrings` 가 켜져 있어야 그 글을 싣는다. 그래서 요청 · 응답 스키마는 `BaseModel` 대신 `app/api/schema_base.py` 의 `ApiSchema` 를 상속한다. 빠뜨리면 `test_openapi_shape.py` 가 잡는다.

## 5. 목도 같은 코드로

웹 목(`apps/web/src/mocks/handlers/`)은 같은 상황에 같은 상태 코드를 낸다. 목 계약 테스트(`mocks/contract.test.ts`)가 그 코드를 건다.
