/**
 * 되물은 run → 그 질문이 어느 아이 것인가. 서버의 pending store 를 흉내 낸다
 * (`apps/api/app/api/runs/pending_reply.py` · #175).
 *
 * - 질문을 내보내기 **직전에** 넣는다 — 서버도 `done` 앞에 넣어서, `note` 를 받자마자 보낸 답도 찾는다
 * - 답이 오면 **한 번만** 꺼낸다 — 같은 질문에 두 번 답하면 관찰이 두 행이 된다
 * - 이어받기 run 이 `failed` 로 끝나면 되돌려 둔다 — 같은 답으로 다시 시도할 수 있어야 한다
 * - 15분 만료 · 재시작은 흉내 내지 않는다. 그 400 은 `reply_unavailable` 시나리오가 만든다
 * - 지난 대화의 `reply_open` 도 이 표로 정한다 (`handlers/chat.ts`) — 그래서 `runs.ts` 밖에 둔다
 *
 * 🚨 프로세스 수명만큼 산다 — `resetSubmittedInputs()` 가 비운다.
 */
export const pendingReplies = new Map<string, string>();
