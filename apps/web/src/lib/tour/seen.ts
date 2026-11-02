"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { api, qk, type Me } from "@/lib/api";

import { seenStorageKey, type TourKey } from "./types";

/**
 * 화면 투어를 "봤음" 기록 (#632 · `docs/web/screen-tour-v1.md` §2).
 *
 * 🚨 **정본은 서버다** — 계정 · 화면 단위로 별도 테이블에 두기로 백엔드와 합의했다. 엔드포인트가 아직
 *    없어서 지금은 기기(`localStorage`)에 둔다. 서버가 생기면 **이 파일의 `readSeen` · `writeSeen` ·
 *    `clearSeen` 셋만** API 호출로 바꾼다. 화면과 투어 컴포넌트는 아래 훅만 알고, 저장소를 모른다.
 *    그래서 지금부터 TanStack Query 로 감싸 둔다 — 서버 상태가 될 값이라 Zustand 에 두지 않는다
 *    (`apps/web/CLAUDE.md` §3 "서버 상태 vs 클라이언트 상태").
 * 🚨 **계정마다 따로 센다.** 한 기기에서 다른 보호자로 다시 로그인하면 그 사람은 아직 안 본 것이다.
 *    그래서 키에 보호자 id 가 들어간다 (토큰이 아니라 id 라 저장해도 되는 값이다).
 * ⚠️ 기기 저장이라 앱 웹뷰와 휴대폰 브라우저에서 따로 센다 — 서버로 옮기면 풀린다.
 * 🚨 **읽고 쓰기가 실패해도 화면을 막지 않는다.** 사생활 보호 모드 · 저장소 차단에서는 접근자가
 *    던진다. 그때는 "안 봤음" 으로 읽어 투어가 다시 뜰 뿐이다.
 */

async function readSeen(parentId: string): Promise<TourKey[]> {
  try {
    const raw = localStorage.getItem(seenStorageKey(parentId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((v): v is TourKey => typeof v === "string") : [];
  } catch {
    return [];
  }
}

async function writeSeen(parentId: string, key: TourKey): Promise<void> {
  const seen = await readSeen(parentId);
  if (seen.includes(key)) return;
  try {
    localStorage.setItem(seenStorageKey(parentId), JSON.stringify([...seen, key]));
  } catch {
    // 위 머리말 — 저장하지 못하면 다음에 다시 뜬다.
  }
}

async function clearSeen(parentId: string): Promise<void> {
  try {
    localStorage.removeItem(seenStorageKey(parentId));
  } catch {
    // 위 머리말.
  }
}

function useParentId(): string | undefined {
  const me = useQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me") });
  return me.data?.id;
}

/** 본 투어 목록. 보호자를 알기 전과 읽는 동안은 `undefined` 다 — 그동안 자동으로 띄우지 않는다. */
export function useSeenTours(): ReadonlySet<TourKey> | undefined {
  const parentId = useParentId();
  const seen = useQuery({
    queryKey: qk.tours(parentId ?? ""),
    queryFn: () => readSeen(parentId!),
    enabled: parentId !== undefined,
    staleTime: Infinity,
  });
  // 🚨 `select` 로 Set 을 만들지 않는다 — 인라인 함수라 그릴 때마다 새 Set 이 나와 받는 쪽 효과가 매번 다시 돈다.
  const keys = seen.data;
  return useMemo(() => (keys ? new Set(keys) : undefined), [keys]);
}

/** 본 것으로 남긴다. 🚨 그만 본 것도 본 것이다 (`docs/web/screen-tour-v1.md` §2). */
export function useMarkTourSeen(): (key: TourKey) => void {
  const parentId = useParentId();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (key: TourKey) => writeSeen(parentId!, key),
    onSettled: () => queryClient.invalidateQueries({ queryKey: qk.tours(parentId ?? "") }),
  });
  return (key) => {
    if (parentId === undefined) return;
    // 🚨 기다리지 않고 먼저 캐시에 얹는다 — 닫자마자 같은 화면이 다시 그려질 때 투어가 한 번 더 뜨면 안 된다.
    //    되돌릴 수 있는 값이라 낙관적 업데이트가 허용된다 (승인 게이트가 아니다).
    queryClient.setQueryData<TourKey[]>(qk.tours(parentId), (old) =>
      old?.includes(key) ? old : [...(old ?? []), key],
    );
    mutation.mutate(key);
  };
}

/**
 * 설정 › 고객센터 "화면 설명 다시 보기". 다음에 각 화면에 들어가면 다시 뜬다.
 * 🚨 보호자를 알기 전에는 누를 수 없다(`ready`) — 누구의 기록을 지우는지 모른다.
 */
export function useResetTours() {
  const parentId = useParentId();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (id: string) => clearSeen(id),
    onSuccess: (_data, id) => queryClient.setQueryData<TourKey[]>(qk.tours(id), []),
  });
  return {
    ready: parentId !== undefined,
    done: mutation.isSuccess,
    reset: () => {
      if (parentId !== undefined) mutation.mutate(parentId);
    },
  };
}
