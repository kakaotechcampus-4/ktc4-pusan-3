"use client";

/**
 * 한 사용자 동작에 키 하나.
 *
 * 🚨 이 훅이 있는 이유는 하나다 — **재시도가 같은 키를 써야** 중복 실행이 막힌다.
 *    `mutationFn: () => confirmEvent(id, newIdempotencyKey())` 처럼 호출 지점에서 키를 만들면
 *    "확정은 됐는데 응답을 못 받아 다시 누른" 경우에 서버는 두 번째를 새 요청으로 본다.
 *    그러면 키를 붙인 의미가 없다.
 *
 * 🚨 서버 컴포넌트에서 쓸 수 없으므로 `@/lib/api` 배럴에 넣지 않았다.
 *    `import { useIdempotencyKey } from "@/lib/api/use-idempotency-key"` 로 직접 가져온다.
 *
 * ```tsx
 * const idem = useIdempotencyKey();
 * const mutation = useMutation({
 *   mutationFn: () => confirmEvent(eventId, idem.current()),
 *   onSuccess: () => { idem.rotate(); ... },   // 성공했을 때만 다음 동작용으로 새 키
 * });
 * ```
 * 실패·타임아웃 뒤 다시 누르면 `current()` 가 같은 키를 돌려준다. 그게 재시도다.
 */

import { useState } from "react";

import { createIdempotencyKeyHolder, type IdempotencyKeyHolder } from "./idempotency";

export type { IdempotencyKeyHolder };

/**
 * 🚨 본문이 바뀌면 키도 바뀌어야 하는 동작은 `current(text)` 로 본문을 넘긴다
 *    (한 줄 입력 · 사진 설명처럼 사용자가 고쳐 쓸 수 있는 것). 자세한 이유는 `idempotency.ts`.
 */
export function useIdempotencyKey(): IdempotencyKeyHolder {
  // 🚨 렌더마다 새로 만들면 키가 매번 바뀐다 — 재시도가 새 요청이 되어 장치가 통째로 무의미해진다.
  //    `useState` 의 게으른 초기화로 컴포넌트 수명 동안 **한 번만** 만든다. `useMemo` 는 React 가
  //    버릴 수 있어서 안 되고, `useRef` 는 렌더 중 읽기를 react-hooks/refs 가 막는다.
  const [holder] = useState(createIdempotencyKeyHolder);
  return holder;
}
