"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { api, isApiError, qk, type InviteAcceptResponse } from "@/lib/api";
import {
  INVITE_CODE_LENGTH,
  formatInviteCode,
  isInviteCodeComplete,
  normalizeInviteCode,
} from "@/lib/invite-code";

/**
 * 초대 코드로 참여. **아이가 0명인 계정이 00-1 에서 들어온다.**
 *
 * 🚨 **여기서 아이 동의를 받지 않는다** (#96). 그 아이에 대한 법정대리인 동의는 아이를
 *    등록한 보호자가 이미 했다 — 법정대리인이 아닌 사람에게 법정대리인 동의를 받으면
 *    그 동의가 무효다. 대신 **무엇을 보게 되는지 알린다.** 동의가 아니라 안내다.
 *
 * 🚨 **로그인 뒤에 입력한다.** 링크를 먼저 열고 로그인하는 흐름이 아니다 — 카카오 왕복을
 *    건너는 동안 `sessionStorage` 가 살아 있다는 보장이 없어서, 초대 토큰만이 아니라
 *    `bind` 까지 잃으면 로그인 자체가 깨진다 (`lib/invite-code.ts` 머리말).
 *    그래서 수락은 `Bearer` 를 요구하는 평범한 호출 하나다 — 무인증 엔드포인트가 늘지 않는다.
 */
export default function InvitePage() {
  return (
    <AuthGate>
      <InviteScreen />
    </AuthGate>
  );
}

/**
 * 🚨 **문구는 프론트가 만든다.** 서버 `message` 를 그대로 띄우지 않는다 — 코드마다 다음
 *    행동이 다른데(다시 확인 / 새 코드 요청 / 등록된 아이 확인) 서버 문장은 그 안내를
 *    담지 않는다. ⚠️ 코드 이름은 확정 전이다 (`lib/api/errors.ts` · #96).
 */
const ERROR_MESSAGE: Record<string, string> = {
  invite_not_found: "이 코드를 찾을 수 없어요. 받은 코드를 다시 확인해 주세요.",
  invite_expired: "코드 기한이 지났어요. 초대한 분에게 새 코드를 받아 주세요.",
  invite_used: "이미 사용된 코드예요. 초대한 분에게 새 코드를 받아 주세요.",
  child_already_exists: "이미 등록한 아이가 있어요. 한 보호자는 아이 한 명만 등록할 수 있어요.",
  too_many_attempts: "여러 번 틀렸어요. 잠시 후에 다시 시도해 주세요.",
};

const DEFAULT_ERROR_MESSAGE = "초대를 수락하지 못했어요. 잠시 후 다시 시도해 주세요.";

function InviteScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();

  /** 🚨 정본은 **정규화된 값**이다. 화면에만 네 자씩 끊어 보여준다. */
  const [code, setCode] = useState("");

  const accept = useMutation({
    mutationFn: (value: string) =>
      api.post<InviteAcceptResponse>(`/invites/${encodeURIComponent(value)}/accept`, {}),
    onSuccess: async (child) => {
      await queryClient.invalidateQueries({ queryKey: qk.me() });
      router.replace(`/child/${child.child_id}/home`);
    },
  });

  const ready = isInviteCodeComplete(code);

  const failure = accept.isError
    ? isApiError(accept.error)
      ? (ERROR_MESSAGE[accept.error.code] ?? DEFAULT_ERROR_MESSAGE)
      : DEFAULT_ERROR_MESSAGE
    : null;

  return (
    <Screen className="gap-6">
      <div>
        <PageTitle>
          받은 코드를
          <br />
          입력해 주세요
        </PageTitle>
        <p className="text-body text-ink-muted mt-3">
          아이를 등록한 보호자가 만든 {INVITE_CODE_LENGTH}자리 코드예요. 한 번만 쓸 수 있어요.
        </p>
      </div>

      <TextInput
        label="초대 코드"
        hint="대소문자와 하이픈은 신경 쓰지 않아도 돼요."
        value={formatInviteCode(code)}
        onChange={(e) => {
          setCode(normalizeInviteCode(e.target.value));
          if (accept.isError) accept.reset();
        }}
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        inputMode="text"
        className="text-center tracking-[0.2em] uppercase"
      />

      {/*
        🚨 **동의가 아니라 안내다** (#96). 체크박스를 두지 않는다 — 여기에 확인 체크를
           세우면 법정대리인 동의처럼 읽히는데, 이 사람은 법정대리인이 아니다.
      */}
      <Card>
        <p className="text-body-sm text-ink">연결되면 이런 것을 함께 보게 돼요</p>
        <ul className="text-body-sm text-ink-muted marker:text-ink-subtle mt-2 flex list-disc flex-col gap-1.5 pl-5">
          <li>아이의 기록과 쌓인 기억</li>
          <li>보호자가 입력한 알레르기 · 건강 정보</li>
          <li>등록된 일정과 준비물</li>
        </ul>
        <p className="text-caption text-ink-subtle mt-3">
          아이를 등록한 보호자만 할 수 있는 일이 따로 있고, 연결은 설정에서 끊을 수 있어요.
        </p>
      </Card>

      {/* 🚨 바닥에 붙이지 않는다. 내용이 셋뿐인 화면에서 `mt-auto` 는 안내와 버튼 사이에
          빈 화면을 한 폭 만든다 — 버튼은 내용 바로 뒤를 따라간다 (05 화면과 같은 규칙). */}
      <div className="flex flex-col gap-3">
        {failure ? <CardFailed>{failure}</CardFailed> : null}

        <Button block onClick={() => accept.mutate(code)} disabled={!ready || accept.isPending}>
          {accept.isPending ? <Spinner /> : null}
          {accept.isPending ? "연결하는 중…" : "연결하기"}
        </Button>
        <button
          type="button"
          className="text-button text-ink-muted min-h-touch"
          onClick={() => router.replace("/start")}
        >
          코드가 없어요
        </button>
      </div>
    </Screen>
  );
}
