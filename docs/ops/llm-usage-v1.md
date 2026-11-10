# LLM 사용량 기록 v1 — 보호자 · run · 모델별 토큰 (BM 산정)

문서 목적: BM 산정에 쓸 "보호자 1인당 LLM 토큰 사용량"을 어떻게 남기는지 결정과 근거를 고정한다.
서버 로그 적재 · 에러 알림은 [`logging-alerts-v1.md`](logging-alerts-v1.md) 가 정본이고, 이 문서는 그 위에 **숫자를 보호자에게 묶는 방법**만 다룬다.

작성일: 2026-11-04 · 저장 · 귀속 단계 반영 2026-11-09 (#710)
담당: 이시하 (계약 · 기록 지점) · 김명성 (저장 · 귀속 · 정리)
선행: 루트 CLAUDE.md §2(원문 대신 id) · [`safety/retention-v1.md`](../safety/retention-v1.md)

## 0. 지금 어디까지 되어 있나

| 구분 | 상태 |
| --- | --- |
| 계약 — `core/llm_usage.py` (`attribute` · `record` · `drain`) | 구현됨 |
| 기록 지점 — LLMClient · Curator 판정 · 임베딩 · 식단 OCR · 공지 사진 읽기 | 구현됨 |
| 호출 지점 목록 테스트 | 구현됨 (`tests/unit/core/test_llm_usage_sites.py`) |
| 저장 — `llm_usage` 테이블(`domains/usage/`) · writer(`api/llm_usage_writer.py`, lifespan) · 90일 정리(`workers/cleanup.py`) | 구현됨 (#710). 배포 때 `make deploy-migrate` 한 번 |
| 귀속 — 입력 run · 사진 run · 05 다시 만들기 · 기록 삭제 · 삭제 확정에서 `attribute()` | 구현됨 (#710) — §3 |

## 1. 결정

| 무엇 | 어떻게 | 왜 |
| --- | --- | --- |
| **어디에 남기나** | DB 테이블 `llm_usage` (호출 1번에 1행). 기존 `llm call` 로그 줄은 디버깅용으로 그대로 | 보호자별 · 월별 분포는 SQL 로 뽑는다. journald 글자 로그는 다른 로그와 섞이고 용량 상한에 잘리고 파싱이 깨지기 쉽다 |
| **보호자는 어떻게 아나** | contextvar 귀속 (`attribute`) | LLMClient 는 Agent 안에서 만들어져 run · 보호자를 모른다. 시그니처마다 `parent_id` 를 넘기면 Agent 전부가 바뀐다 |
| **기록 방법** | `record()` 는 동기 · 즉시 반환(큐에 넣기). DB 쓰기는 api 의 writer 가 모아서 | OCR 은 동기 클라이언트라 스레드에서 돌아 `await` 할 수 없다. run 이 끝난 뒤 끝나는 Curator 기록도 빠지지 않는다 |
| **계약 위치** | `app/core/` | agents · providers · api · domains 가 다 쓴다. providers 는 agents 를, api 는 entrypoint 밖의 agents 를 import 할 수 없다 |
| **모델** | 응답에 찍힌 실제 모델 우선, 없으면 설정값. `role`(Agent 이름 `supervisor` · `memory` · `food` · `activity` · `growth` · `health` · `curator_judge` · `embedding` · `meal_ocr` · `notice_ocr`) 과 `provider`(host 만) 를 따로 남김 | 게이트웨이가 별칭을 실제 모델로 풀 수 있고 과금은 실제 모델 기준이다. 도메인 Agent · OCR 마다 모델이 다르다 |
| **보호자 없이 부르면** | 서버(writer 켜짐)에서는 저장하지 않고 WARNING 한 줄 | 운영에서 0건이 정상이다. 1건이라도 있으면 귀속 지점이 빠진 것이다 |
| **보호자가 탈퇴하면** | 행을 지우지 않고 `parent_id` 만 비운다(SET NULL). 숫자는 90일 정리가 지운다. 탈퇴 순간 돌던 run · Curator 가 늦게 넣는 기록도 보호자 칸만 비워 저장한다 | 월 1회 제공처 콘솔 합계와 대조하려면 그달 탈퇴한 보호자의 토큰도 있어야 한다 (11-08 PM). 처리방침의 "인공지능 요청 기록 3개월 보관 뒤 삭제" 와도 맞다 |
| **셀 값이 없는 응답** | 기록하지 않는다 | 0 토큰 행이 평균을 흐린다 |
| **실패해도** | 호출은 정상으로 돌아온다 | 기록 때문에 추천 · run 이 실패하면 안 된다 |

🚨 원문 · 프롬프트 · 응답 · tool 인자는 로그에도 테이블에도 없다. 남기는 것은 id · 이름 · 숫자뿐이다 (루트 §2).

## 2. 테이블 `llm_usage`

| 컬럼 | 값 |
| --- | --- |
| `parent_id` NULL 허용 → `parent` ON DELETE **SET NULL** | 귀속 보호자. 탈퇴하면 비어서 `NULL` 이 된다 |
| `run_id` NOT NULL, `source` NOT NULL CHECK (`input` · `suggest` · `photo` · `delete`) | 처리 단위와 창구 — §3. `source` 는 창구이고 모델 구분은 `role` 이 한다 |
| `role` · `provider` · `model` NOT NULL | 누가 · 어느 게이트웨이의 · 어떤 모델이 |
| `prompt_tokens` · `cached_prompt_tokens` · `completion_tokens` · `reasoning_tokens` · `latency_ms` | `cached` 는 `prompt` 에, `reasoning` 은 `completion` 에 **이미 포함**된 값이다. 더하면 두 번 센다 |
| `occurred_at` (UTC) | 집계는 `AT TIME ZONE 'Asia/Seoul'` |

토큰 값이 None · 문자열 · 음수로 오면 `record()` 가 0 으로 담는다. 나중 INSERT 가 한 행 때문에 묶음째 실패하지 않게 하는 것이고, 호출 쪽에서 `int()` 로 감싸다 터지는 일도 막는다.
식단 OCR 의 `cached_prompt_tokens` 도 `prompt_tokens` 에 포함된 값으로 본다 — `scripts/meal_ocr_eval.py` 가 같은 식(`prompt − cached`)으로 비용을 계산한다.

`child_id` 는 넣지 않는다. BM 단위는 보호자고, 넣으면 "아이와 함께 지운다" 대상이 하나 더 생긴다.
탈퇴한 보호자의 행도 `run_id` 는 그대로 둔다 — 이어 볼 계정이 없어서 run 수 집계 · 디버깅에만 쓴다.
인덱스는 `(parent_id, occurred_at)`(보호자별 집계 · 탈퇴 때 SET NULL) · `(occurred_at)`(90일 정리) · `(run_id)`(run 하나 디버깅)다.

## 3. 귀속 규칙 — 창구마다 `attribute()` 를 건다

| 창구 | `source` | `run_id` | 거는 곳 |
| --- | --- | --- | --- |
| 입력 run (`runner.agent_job`, 이어받기 포함) | `input` | `channel.run_id` | `agent_job` 의 job |
| 사진 run (`photo_run.photo_job`) — 공지 읽기 · 식단표 읽기 | `photo` | `channel.run_id` | `photo_job` 의 job |
| 05 다시 만들기 (`entrypoint.suggest`) | `suggest` | 실패한 추천을 다시 만드는 요청(`run_id` 가 있다)은 **그 run 의 `run_id`**, 처음 요청은 그 요청의 `request_id` | `routers/suggestions.py` |
| 기록 삭제(`observations.py`) · 대화 삭제 확정(`delete_proposals.py`) 뒤의 Curator | `delete` | 그때 만든 uuid4 hex | 두 라우터의 `_trigger_curator` |

사진 한 장이 공지 읽기와 식단표 읽기를 함께 부르므로 창구는 하나(`photo`)다. 모델은 `role`(`notice_ocr` · `meal_ocr`)이 가른다.
기록 삭제 · 삭제 확정은 run 이 없지만 Curator(임베딩 · 판정)를 띄운다 — 귀속을 따로 건다.

Curator 는 run 이 끝난 뒤에 끝나도 그 run 으로 남는다 (asyncio 태스크는 만들 때 context 를 복사한다). 구분은 `role` 로 한다.

## 4. 새 LLM 호출 지점을 만들 때

1. 응답의 usage 를 읽어 `llm_usage.record(...)` 를 부른다. 예외를 던지는 검사(출력 상한 · 거절 · `message` 없음 · 개수 · 차원 · 응답 모양)보다 **앞**에 둔다. 검사에 걸려 버리는 응답도 토큰은 이미 과금됐다. 예외: HTTP 오류(4xx · 5xx)는 기록하지 않는다 — SDK 가 오류 응답을 먼저 예외로 바꿔 사용량을 읽을 수 없고, 직접 부르는 Curator 판정도 같은 기준으로 맞춘다.
2. 그 지점을 부르는 창구에서 `llm_usage.attribute(...)` 를 건다. §3 표에 한 줄을 더한다.
3. 빼 먹으면 `tests/unit/core/test_llm_usage_sites.py` 가 실패한다 (`completions.create` · `embeddings.create` · `OpenAI(` · `"/decisions"` 가 있는 파일은 `record(` 가 있어야 한다).

## 5. 사진 run — 공지 · 식단표 읽기

식단표 OCR 과 공지 사진 읽기는 도메인 Agent 와 모델이 다르다 (`MEAL_OCR_MODEL` · 공지 읽기 모델). 둘 다 사진 run(`photo_run.photo_job` → `entrypoint.read_photo`) 한 곳에서만 불리고, 그 job 이 `source="photo"` 로 귀속을 건다. 기록 한 줄은 `image_ocr.py` · `notice/extractor.py` 에 이미 있다.

- 🚨 `read_photo` 는 `asyncio.to_thread` 로 부른다. 이것만 context 를 스레드로 복사한다. `loop.run_in_executor` · 직접 만든 `ThreadPoolExecutor` 는 귀속이 사라져 저장되지 않고 WARNING 만 남는다 (`test_run_in_executor_는_귀속을_넘기지_못한다` 가 이를 고정한다).
- reader 는 요청마다 만든다 (`notice_ocr.read_photo` 가 그렇게 한다). `calls` · `total_usage` 가 인스턴스에 쌓여서, 하나를 같이 쓰면 호출 상한이 서버 전체 값이 되고 보호자 것이 섞인다. 보호자별 숫자는 테이블에서만 본다.
- 식단 OCR 을 사진 run 밖에서 따로 부르는 라우트가 생기면 §3 에 한 줄을 더하고 그 라우트에서 `attribute()` 를 건다.
- 테스트 — `tests/unit/api/test_usage_attribution.py` 가 사진 run 이 `source="photo"` · 요청한 보호자 · 그 run 의 `run_id` 로 귀속되는지 본다.

## 6. 서버에서 고려할 것 (인프라)

저장 단계(#710)가 들어간 뒤의 기준이다.

| 항목 | 내용 |
| --- | --- |
| 배포 | 이미지를 올린 뒤 `make deploy-migrate`. 컨테이너 · 환경변수 · cron · 볼륨은 늘지 않는다. 새 api 가 뜬 뒤 migrate 가 끝나기 전 몇 초의 기록은 INSERT 실패로 버려질 수 있다 (ERROR 1줄) |
| 용량 | 가정: 하루 run 1천 건 · run 당 10행 · 행당 약 300B → 90일에 약 300MB. 디스크 50GB 기준 문제없다. 실측 뒤 고친다 |
| 종료 · 유실 | SIGTERM 이면 lifespan 이 writer 를 멈추며(취소) 쓰던 묶음을 큐에 되돌리고 남은 큐를 마지막으로 쓴다. 마지막 flush 는 5초까지만 기다린다(docker 정지 유예 10초 안). SIGKILL · OOM 이면 최대 2초분을 잃는다. 종료 뒤에 끝나는 run · Curator 의 기록은 이미 `disable()` 이라 남지 않는다 — BM 추정에는 감수 |
| 저장 실패 | 탈퇴와 겹친 FK 위반은 새 세션으로 한 번 더 쓴다. 연결 · 풀 오류는 묶음을 큐 뒤에 되돌리고 다음 주기에 다시 쓴다 — 큐 상한(10,000건)보다 오래 못 쓰면 넘치는 만큼만 버린다. 그 밖의 실패(스키마 버그 · 값 오류)는 그 묶음(최대 500건)을 버리고 그 주기를 끝낸다. 첫 실패만 ERROR(Discord), 이어지면 WARNING, 고쳐지면 INFO 한 줄 |
| 값 검사 | `record()` 가 숫자는 int4 범위로(넘으면 0), 글자 칸은 NUL 을 지워서, `Source` 밖의 창구 값은 큐에 들이지 않고 WARNING 으로 걸러낸다 — 한 행이 같은 묶음의 500행을 실패시키지 않게. `tests/unit/core/test_llm_usage_sites.py` 가 `attribute(source=...)` 에 적힌 글자도 `Source` 와 대조한다 |
| 정리 | 기존 하루 한 번 `python -m app.workers.cleanup` 이 90일 지난 행도 지운다 (cron 추가 없음). 처방침이 약속한 정리(세션 · 동의 증빙 · 위치 기록 · 삭제 이력)를 커밋한 **다음 다른 트랜잭션**에서 한다 — 사용량 정리가 실패해도(예: 배포 때 migrate 전이라 표가 없다) 앞의 정리는 되감기지 않고 ERROR 로만 알린다. 배치 로그의 `90일 지난 LLM 사용량 정리: N건` 으로 확인한다 |
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
    AND parent_id IS NOT NULL   -- 탈퇴한 보호자의 묶음이 한 사람으로 세지지 않게
  GROUP BY parent_id)
SELECT count(*) AS active_parents,
       avg(input_uncached + input_cached + output) AS avg_tokens,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY input_uncached + input_cached + output) AS p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY input_uncached + input_cached + output) AS p90
FROM per_user;

-- 월 · 창구 · 역할 · 모델별 합계 — 단가표(입력 · 캐시 입력 · 출력)를 곱해 비용으로
SELECT date_trunc('month', occurred_at AT TIME ZONE 'Asia/Seoul') AS month,
       source, role, provider, model, count(*) AS calls,
       count(*) FILTER (WHERE parent_id IS NULL) AS calls_withdrawn,  -- 콘솔 합계 대조용
       sum(prompt_tokens - cached_prompt_tokens) AS input_uncached,
       sum(cached_prompt_tokens) AS input_cached, sum(completion_tokens) AS output
FROM llm_usage GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3;
```

단가표는 DB 에 두지 않는다. 제공처 가격이 바뀌면 BM 시트만 고친다. Elice 판정(`curator_judge`)은 출력이 과금되지 않는다.

## 8. 정해진 것 · 열린 것

- **보관 기간 · 탈퇴 시 처리 (PM, 2026-11-08 확정)** — 90일 보관은 처리방침 제1장 ③ "인공지능 요청 기록 3개월" 과 같은 값이라 문구로 충분하다. 탈퇴하면 행을 지우는 대신 `parent_id` 만 비운다(SET NULL) — 제공처 콘솔 합계와 맞추려면 탈퇴한 보호자의 토큰도 남아 있어야 해서다. [retention-v1](../safety/retention-v1.md) 에 행을 올렸다.
- **처리방침 제1장 ⑥ 문구 (PM, #166)** — 이 표가 남기는 것(어느 모델을 썼는지 · 토큰 수 · 보호자 계정과의 연결 · 비용과 이용 한도를 정하는 데 쓴다는 것)을 다음 판에 한 줄 더한다. 코드는 그대로다.
- **writer 위치** — api lifespan (`main.py`). 프로세스마다 큐 · writer 가 하나씩이라 `--workers` 를 늘려도 맞다.
- **열림** — 90일보다 긴 BM 데이터가 필요하면 `parent_id` 없는 월별 합계만 따로 남길지 (루트 §10). 지금은 정하지 않았고, 90일이 지나면 숫자도 지워진다.
- **배포 순서 (PM 확인 필요)** — 이 표는 보호자 계정에 묶인 모델 · 토큰 수를 남긴다. 처방침 제1장 ⑥ 에는 아직 "처리 건수와 걸린 시간" 만 적혀 있어서, 보호자 연결을 남기는 항목은 다음 판(#166)에 더해진다. PM 이 "코드는 그대로 가도 된다" 고 했지만 **처방침 새 판 게시보다 먼저 운영에 배포해도 되는지** 는 정하지 않았다 (루트 §10 — 미정을 기본값으로 채우지 않는다). 정해지기 전까지 dev 에서만 켜 둘지는 PM 이 정한다.
