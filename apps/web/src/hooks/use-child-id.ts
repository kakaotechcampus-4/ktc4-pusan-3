"use client";

import { useParams } from "next/navigation";

/**
 * 지금 화면이 보고 있는 아이. 정본은 URL 이다 (스토어가 아니다 — child-scope.tsx 참고).
 * 서버 컴포넌트에서는 이 훅 대신 params 를 그대로 쓴다.
 */
export function useChildId(): string {
  const params = useParams<{ childId: string }>();
  const childId = params?.childId;

  if (!childId) {
    throw new Error("useChildId 는 /c/[childId] 아래에서만 쓸 수 있다.");
  }
  return childId;
}
