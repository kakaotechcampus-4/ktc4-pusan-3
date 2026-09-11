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

import { useCallback, useRef } from "react";

import { newIdempotencyKey, type IdempotencyKey } from "./idempotency";

export interface IdempotencyKeyHolder {
  /** 지금 동작의 키. 여러 번 불러도 같은 값이다. */
  current: () => IdempotencyKey;
  /** 다음 동작으로 넘어간다. 🚨 성공 응답을 받은 뒤에만 부른다. */
  rotate: () => void;
}

export function useIdempotencyKey(): IdempotencyKeyHolder {
  const keyRef = useRef<IdempotencyKey | null>(null);

  const current = useCallback((): IdempotencyKey => {
    keyRef.current ??= newIdempotencyKey();
    return keyRef.current;
  }, []);

  const rotate = useCallback((): void => {
    keyRef.current = null;
  }, []);

  return { current, rotate };
}
