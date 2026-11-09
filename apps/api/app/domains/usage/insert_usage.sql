-- 사용량 묶음 INSERT (#710). 칸마다 배열 하나를 받아 unnest 로 행을 펼친다 — 묶음이 커도 문장이 하나다.
--
-- 🚨 LEFT JOIN 이다. 탈퇴한 보호자의 늦은 기록(탈퇴 때 돌던 run · Curator)은 버리지 않고 보호자 칸만
--    비워 넣는다 — 제공처 콘솔 합계와 맞춰 보려면 그 토큰도 있어야 한다. 그냥 INSERT 하면 FK 위반 한
--    행 때문에 같은 묶음 전체가 실패하고, JOIN 이면 그 행이 조용히 빠진다.
-- 보호자 행이 이 문장 도중에 지워지는 경우의 FK 위반은 호출자(writer)가 한 번 다시 시도해서 푼다.
INSERT INTO llm_usage (
    occurred_at, parent_id, run_id, source, role, provider, model,
    prompt_tokens, cached_prompt_tokens, completion_tokens, reasoning_tokens, latency_ms
)
SELECT
    v.occurred_at, p.id, v.run_id, v.source, v.role, v.provider, v.model,
    v.prompt_tokens, v.cached_prompt_tokens, v.completion_tokens, v.reasoning_tokens, v.latency_ms
FROM unnest(
    CAST(:occurred_at AS timestamptz[]),
    CAST(:parent_id AS uuid[]),
    CAST(:run_id AS text[]),
    CAST(:source AS text[]),
    CAST(:role AS text[]),
    CAST(:provider AS text[]),
    CAST(:model AS text[]),
    CAST(:prompt_tokens AS int[]),
    CAST(:cached_prompt_tokens AS int[]),
    CAST(:completion_tokens AS int[]),
    CAST(:reasoning_tokens AS int[]),
    CAST(:latency_ms AS int[])
) AS v(
    occurred_at, parent_id, run_id, source, role, provider, model,
    prompt_tokens, cached_prompt_tokens, completion_tokens, reasoning_tokens, latency_ms
)
LEFT JOIN parent p ON p.id = v.parent_id
