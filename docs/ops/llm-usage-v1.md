# LLM 사용량 기록 v1 — 보호자 · run · 모델별 토큰 (BM 산정)

문서 목적: BM 산정에 쓸 "보호자 1인당 LLM 토큰 사용량"을 어떻게 남기는지 결정과 근거를 고정한다.
서버 로그 적재 · 에러 알림은 [`logging-alerts-v1.md`](logging-alerts-v1.md) 가 정본이고, 이 문서는 그 위에 **숫자를 보호자에게 묶는 방법**만 다룬다.

작성일: 2026-11-04
담당: 이시하 (계약 · 기록 지점) · 김명성 (저장 · 귀속 · 정리)
선행: 루트 CLAUDE.md §2(원문 대신 id) · [`safety/retention-v1.md`](../safety/retention-v1.md)

## 0. 지금 어디까지 되어 있나

| 구분 | 상태 |
| --- | --- |
| 계약 — `core/llm_usage.py` (`attribute` · `record` · `drain`) | 구현됨 |
| 기록 지점 — LLMClient · Curator 판정 · 임베딩 · 식단 OCR | 구현됨. 단 **writer 가 꺼져 있어** 아직 아무것도 쌓이지 않는다 |
| 호출 지점 목록 테스트 | 구현됨 (`tests/unit/core/test_llm_usage_sites.py`) |
| 저장 — `llm_usage` 테이블 · writer · 90일 정리 | **BE 이슈** (아직 없음) |
| 귀속 — 입력 run · 05 다시 만들기에서 `attribute()` 걸기 | **BE 이슈** (아직 없음) |
| 식단 OCR 라우트 | 라우트가 생길 때 §5 대로 |

## 1. 결정

| 무엇 | 어떻게 | 왜 |
| --- | --- | --- |
| **어디에 남기나** | DB 테이블 `llm_usage` (호출 1번에 1행). 기존 `llm call` 로그 줄은 디버깅용으로 그대로 | 보호자별 · 월별 분포는 SQL 로 뽑는다. journald 글자 로그는 다른 로그와 섞이고 용량 상한에 잘리고 파싱이 깨지기 쉽다 |
| **보호자는 어떻게 아나** | contextvar 귀속 (`attribute`) | LLMClient 는 Agent 안에서 만들어져 run · 보호자를 모른다. 시그니처마다 `parent_id` 를 넘기면 Agent 전부가 바뀐다 |
| **기록 방법** | `record()` 는 동기 · 즉시 반환(큐에 넣기). DB 쓰기는 api 의 writer 가 모아서 | OCR 은 동기 클라이언트라 스레드에서 돌아 `await` 할 수 없다. run 이 끝난 뒤 끝나는 Curator 기록도 빠지지 않는다 |
| **계약 위치** | `app/core/` | agents · providers · api · domains 가 다 쓴다. providers 는 agents 를, api 는 entrypoint 밖의 agents 를 import 할 수 없다 |
| **모델** | 응답에 찍힌 실제 모델 우선, 없으면 설정값. `role`(Agent 이름 · `curator_judge` · `embedding` · `meal_ocr`) 과 `provider`(host 만) 를 따로 남김 | 게이트웨이가 별칭을 실제 모델로 풀 수 있고 과금은 실제 모델 기준이다. 도메인 Agent · OCR 마다 모델이 다르다 |
| **보호자 없이 부르면** | 서버(writer 켜짐)에서는 저장하지 않고 WARNING 한 줄 | 운영에서 0건이 정상이다. 1건이라도 있으면 귀속 지점이 빠진 것이다 |
| **셀 값이 없는 응답** | 기록하지 않는다 | 0 토큰 행이 평균을 흐린다 |
| **실패해도** | 호출은 정상으로 돌아온다 | 기록 때문에 추천 · run 이 실패하면 안 된다 |

🚨 원문 · 프롬프트 · 응답 · tool 인자는 로그에도 테이블에도 없다. 남기는 것은 id · 이름 · 숫자뿐이다 (루트 §2).

## 2. 테이블 `llm_usage` (BE 이슈에서 만든다)

| 컬럼 | 값 |
| --- | --- |
| `parent_id` NOT NULL → `parent` ON DELETE CASCADE | 귀속 보호자 |
| `run_id` NOT NULL, `source` NOT NULL (`input` · `suggest` · `meal_ocr`) | 처리 단위와 창구 — §3 |
| `role` · `provider` · `model` NOT NULL | 누가 · 어느 게이트웨이의 · 어떤 모델이 |
| `prompt_tokens` · `cached_prompt_tokens` · `completion_tokens` · `reasoning_tokens` · `latency_ms` | `cached` 는 `prompt` 에, `reasoning` 은 `completion` 에 **이미 포함**된 값이다. 더하면 두 번 센다 |
| `occurred_at` (UTC) | 집계는 `AT TIME ZONE 'Asia/Seoul'` |

토큰 값이 None · 문자열 · 음수로 오면 `record()` 가 0 으로 담는다. 나중 INSERT 가 한 행 때문에 묶음째 실패하지 않게 하는 것이고, 호출 쪽에서 `int()` 로 감싸다 터지는 일도 막는다.
식단 OCR 의 `cached_prompt_tokens` 도 `prompt_tokens` 에 포함된 값으로 본다 — `scripts/meal_ocr_eval.py` 가 같은 식(`prompt − cached`)으로 비용을 계산한다.

`child_id` 는 넣지 않는다. BM 단위는 보호자고, 넣으면 "아이와 함께 지운다" 대상이 하나 더 생긴다.

## 3. 귀속 규칙 — 창구마다 `attribute()` 를 건다

| 창구 | `source` | `run_id` |
| --- | --- | --- |
| 입력 run (`runner.agent_job`, 이어받기 포함) | `input` | `channel.run_id` |
| 05 다시 만들기 (`entrypoint.suggest`) | `suggest` | 그 요청의 `request_id` |
| 식단 OCR | `meal_ocr` | 업로드 1건의 id |

Curator 는 run 이 끝난 뒤에 끝나도 그 run 으로 남는다 (asyncio 태스크는 만들 때 context 를 복사한다). 구분은 `role` 로 한다.

## 4. 새 LLM 호출 지점을 만들 때

1. 응답의 usage 를 읽어 `llm_usage.record(...)` 를 부른다. 예외를 던지는 검사(출력 상한 · 거절)보다 **앞**에 둔다. 잘린 응답도 과금된다.
2. 그 지점을 부르는 창구에서 `llm_usage.attribute(...)` 를 건다. §3 표에 한 줄을 더한다.
3. 빼 먹으면 `tests/unit/core/test_llm_usage_sites.py` 가 실패한다 (`completions.create` · `embeddings.create` · `OpenAI(` · `"/decisions"` 가 있는 파일은 `record(` 가 있어야 한다).

## 5. 식단 OCR 담당에게

OCR 은 도메인 Agent 와 모델이 다르다 (`MEAL_OCR_MODEL`). 기록 한 줄은 이미 `image_ocr.py` 에 있다. 라우트를 붙일 때 아래만 지킨다.

```python
with llm_usage.attribute(parent_id=parent.parent_id, run_id=upload_id, source="meal_ocr"):
    result = await asyncio.to_thread(reader.read, data, mime_type=mime_type)
```

- 🚨 `asyncio.to_thread` 를 쓴다. 이것만 context 를 스레드로 복사한다. `loop.run_in_executor` · 직접 만든 `ThreadPoolExecutor` 는 귀속이 사라져 저장되지 않고 WARNING 만 남는다 (`test_run_in_executor_는_귀속을_넘기지_못한다` 가 이를 고정한다).
- `read()` 는 동기 OpenAI 호출(최대 300초 × 구간 수)이라 async 라우트에서 그냥 부르면 서버 전체가 멈춘다. 어차피 스레드로 보내야 한다.
- reader 는 요청마다 만든다. `calls` · `total_usage` 가 인스턴스에 쌓여서, 하나를 같이 쓰면 호출 상한이 서버 전체 값이 되고 보호자 것이 섞인다. 보호자별 숫자는 테이블에서만 본다.
- 라우트 테스트에서 `source="meal_ocr"` 이고 `parent_id` 가 요청한 보호자인 행이 남는지 확인한다.

## 6. 서버에서 고려할 것 (인프라)

저장 단계(BE 이슈)가 들어갈 때의 기준이다. 이 PR 만으로는 서버가 바뀌지 않는다.

| 항목 | 내용 |
| --- | --- |
| 배포 | 이미지를 올린 뒤 `make deploy-migrate`. 컨테이너 · 환경변수 · cron · 볼륨은 늘지 않는다. 새 api 가 뜬 뒤 migrate 가 끝나기 전 몇 초의 기록은 INSERT 실패로 버려질 수 있다 (ERROR 1줄) |
| 용량 | 가정: 하루 run 1천 건 · run 당 10행 · 행당 약 300B → 90일에 약 300MB. 디스크 50GB 기준 문제없다. 실측 뒤 고친다 |
| 종료 · 유실 | SIGTERM 이면 남은 큐를 flush 한다. SIGKILL · OOM 이면 최대 2초분을 잃는다 — BM 추정에는 감수 |
| 정리 | 기존 하루 한 번 `python -m app.workers.cleanup` 이 90일 지난 행도 지운다 (cron 추가 없음) |
| journald | `llm call` 로그 줄은 계속 남는다. [retention-v1](../safety/retention-v1.md) 이 서버 로그 3개월을 약속하므로 `MaxRetentionSec=90day` 와 `SystemMaxUse`(`journalctl --disk-usage`)를 확인한다 |
| 조회 · 공유 | psql 로 집계한다. 🚨 `parent_id` 단위 결과를 Discord · Notion 에 붙이지 않는다 — 합계 · 분포만. 외부 수집 서비스로 보내지 않는다 ([logging-alerts-v1 §1](logging-alerts-v1.md#1-결정)) |
| 대조 | 월 1회 제공처 콘솔 합계와 비교한다. 타임아웃(60초) · 취소 · 오류 호출은 응답에 usage 가 없어 우리 쪽에 남지 않지만 과금될 수 있다. 차이 비율을 BM 의 보정값으로 쓴다 |

## 7. 집계 (BM)

```sql
-- 최근 30일 활성 보호자 1인당 토큰 분포
WITH per_user AS (
  SELECT parent_id,
         sum(prompt_tokens - cached_prompt_tokens) AS input_uncached,
         sum(cached_prompt_tokens)                 AS input_cached,
         sum(completion_tokens)                    AS output
  FROM llm_usage
  WHERE occurred_at >= now() - interval '30 days'
  GROUP BY parent_id)
SELECT count(*) AS active_parents,
       avg(input_uncached + input_cached + output) AS avg_tokens,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY input_uncached + input_cached + output) AS p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY input_uncached + input_cached + output) AS p90
FROM per_user;

-- 월 · 창구 · 역할 · 모델별 합계 — 단가표(입력 · 캐시 입력 · 출력)를 곱해 비용으로
SELECT date_trunc('month', occurred_at AT TIME ZONE 'Asia/Seoul') AS month,
       source, role, provider, model, count(*) AS calls,
       sum(prompt_tokens - cached_prompt_tokens) AS input_uncached,
       sum(cached_prompt_tokens) AS input_cached, sum(completion_tokens) AS output
FROM llm_usage GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3;
```

단가표는 DB 에 두지 않는다. 제공처 가격이 바뀌면 BM 시트만 고친다. Elice 판정(`curator_judge`)은 출력이 과금되지 않는다.

## 8. 열린 것

- **보관 기간 · 탈퇴 시 처리 (PM)** — 90일 보관과 탈퇴 시 CASCADE 를 제안한다. [retention-v1](../safety/retention-v1.md) 의 "서버 로그 — 인공지능 요청 기록 3개월" 과 같은 값이다. 정해지기 전에는 retention-v1 을 고치지 않고, 저장 단계(BE 이슈) 때 그 표에 한 줄을 같이 올린다. 90일보다 긴 BM 데이터가 필요하면 `parent_id` 없는 월별 합계만 따로 남길지도 같이 정한다 (루트 §10).
- **writer 위치 (BE)** — api lifespan 에 두는 것으로 가정했다. 프로세스마다 큐 · writer 가 하나씩이라 `--workers` 를 늘려도 맞다.
