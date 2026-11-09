"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { api, qk, type Me, type SeenTours } from "@/lib/api";

import { withSeen } from "./steps";
import { TOUR_KEYS, type TourKey } from "./types";

/**
 * 화면 투어를 "봤음" 기록 (#632 · #696 · `docs/web/screen-tour-v1.md` §2).
 *
 * 🚨 **정본은 서버다** — 계정 · 화면 단위로 `GET` · `PUT` · `DELETE /me/tours` 에 둔다 (#696).
 *    기기마다 따로 세지 않아서, 앱 웹뷰에서 본 투어가 휴대폰 브라우저에서 다시 뜨지 않는다.
 *    화면과 투어 컴포넌트는 아래 훅만 알고 저장소를 모른다.
 * 🚨 **쿼리 키에 보호자 id 를 넣는다.** 서버는 토큰으로 보호자를 가르지만, 한 기기에서 다른 보호자로
 *    다시 로그인했을 때 앞 사람의 캐시가 그대로 읽히면 안 된다.
 * 🚨 **읽기가 실패하면 자동으로 띄우지 않는다** (`undefined` 그대로). 이미 본 사람에게 다시 띄우는 쪽보다
 *    한 번 덜 띄우는 쪽이 낫다. ? 버튼은 그대로 열린다.
 * 🚨 **쓰기가 실패해도 화면을 막지 않는다.** 캐시는 먼저 "봤음" 으로 얹었으니 이번 방문에서는 다시 뜨지
 *    않고, 다음에 서버에서 다시 읽을 때 뜰 뿐이다. 되돌릴 수 있는 값이라 Idempotency-Key 를 쓰지 않는다
 *    (서버가 받지 않는다 · `PUT` · `DELETE` 는 여러 번 보내도 결과가 같다).
 */

function useParentId(): string | undefined {
  const me = useQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me") });
  return me.data?.id;
}

/** 본 투어 목록. 보호자를 알기 전과 읽는 동안, 읽지 못했을 때는 `undefined` 다 — 그동안 자동으로 띄우지 않는다. */
export function useSeenTours(): ReadonlySet<TourKey> | undefined {
  const parentId = useParentId();
  const seen = useQuery({
    queryKey: qk.tours(parentId ?? ""),
    // 화면이 모르는 이름(다른 버전의 서버가 더한 화면)은 버린다 — 이 화면의 투어와 상관이 없다.
    queryFn: async () =>
      (await api.get<SeenTours>("/me/tours")).seen.filter((k) => TOUR_KEYS.includes(k)),
    enabled: parentId !== undefined,
    staleTime: Infinity,
  });
  // 🚨 `select` 로 Set 을 만들지 않는다 — 인라인 함수라 그릴 때마다 새 Set 이 나와 받는 쪽 효과가 매번 다시 돈다.
  const keys = seen.data;
  return useMemo(() => (keys ? new Set(keys) : undefined), [keys]);
}

/** 본 것으로 남긴다. 🚨 그만 본 것도 본 것이다 (`docs/web/screen-tour-v1.md` §2). ? 로 다시 본 것도 보낸다 — 서버가 처음 본 시각을 지킨다. */
export function useMarkTourSeen(): (key: TourKey) => void {
  const parentId = useParentId();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (key: TourKey) => api.put<void>(`/me/tours/${key}`),
    // 실패했으면 서버 값으로 다시 맞춘다. 성공해도 목록이 없으면(첫 읽기를 아래에서 멈췄다) 다시 읽는다.
    // 🚨 호출 단위 콜백이 아니라 여기 둔다 — 화면을 떠나 훅이 내려가도 돈다 (#815 리뷰).
    onSettled: (_data, error) => {
      const queryKey = qk.tours(parentId ?? "");
      if (error || queryClient.getQueryData(queryKey) === undefined) {
        void queryClient.invalidateQueries({ queryKey });
      }
    },
  });
  return (key) => {
    if (parentId === undefined) return;
    const queryKey = qk.tours(parentId);
    // 🚨 읽는 중이면 먼저 멈춘다 — 얹은 값을 늦게 도착한 GET 이 덮으면 같은 투어가 다시 뜬다.
    //    멈춘 읽기는 PUT 이 끝난 뒤 다시 읽는다 (위 `onSettled`).
    void queryClient.cancelQueries({ queryKey });
    // 🚨 기다리지 않고 먼저 캐시에 얹는다 — 닫자마자 같은 화면이 다시 그려질 때 투어가 한 번 더 뜨면 안 된다.
    //    되돌릴 수 있는 값이라 낙관적 업데이트가 허용된다 (승인 게이트가 아니다). 목록이 없으면 만들지 않는다 (`withSeen`).
    queryClient.setQueryData<TourKey[]>(queryKey, (old) => withSeen(old, key));
    mutation.mutate(key);
  };
}

/**
 * 설정 › 고객센터 "화면 설명 다시 보기". 다음에 각 화면에 들어가면 다시 뜬다.
 * 🚨 보호자를 알기 전에는 누를 수 없다(`ready`) — 어느 캐시를 비울지 모른다.
 * 🚨 서버가 지운 뒤에만 "다시 켰어요" 라고 말한다 (`done`). 실패하면 `failed` 로 다시 누를 길을 둔다.
 */
export function useResetTours() {
  const parentId = useParentId();
  const queryClient = useQueryClient();
  // 변수는 보호자 id 다 — 서버는 토큰으로 가르고, 화면은 어느 캐시를 비울지에만 쓴다.
  const mutation = useMutation<void, Error, string>({
    mutationFn: () => api.delete<void>("/me/tours"),
    onSuccess: (_data, id) => queryClient.setQueryData<TourKey[]>(qk.tours(id), []),
  });
  return {
    ready: parentId !== undefined,
    pending: mutation.isPending,
    done: mutation.isSuccess,
    failed: mutation.isError,
    reset: () => {
      if (parentId !== undefined) mutation.mutate(parentId);
    },
  };
}
