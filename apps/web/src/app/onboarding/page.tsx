"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import {
  api,
  qk,
  type CreateChildRequest,
  type CreateChildResponse,
  type Relation,
} from "@/lib/api";

/**
 * 01 첫 진입 — 아이 만들기.
 *
 * 🚨 프로토타입은 **나이**를 드롭다운으로 받지만 여기서는 **생일**을 받는다.
 *    계약서가 `birth_date` 를 받고, 나이 → 생일 환산은 프론트가 날짜를 계산하는 것이라 금지다
 *    (apps/web/CLAUDE.md §4). 나이 문구(`age_display`)는 서버가 만들어 내려준다.
 *
 * 🚨 수집은 별명 · 생일 · 관계까지다. 프로필 질문을 늘리지 않는다 (CLAUDE.md §2 개인정보).
 */

const RELATIONS: Array<{ value: Relation; label: string }> = [
  { value: "mother", label: "엄마" },
  { value: "father", label: "아빠" },
  { value: "grandparent", label: "조부모" },
  { value: "other", label: "그 밖에" },
];

export default function OnboardingPage() {
  return (
    <AuthGate>
      <CreateChildScreen />
    </AuthGate>
  );
}

function CreateChildScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const [nickname, setNickname] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [relation, setRelation] = useState<Relation | null>(null);
  const [errors, setErrors] = useState<{ nickname?: string; birthDate?: string }>({});

  const createChild = useMutation({
    mutationFn: (body: CreateChildRequest) => api.post<CreateChildResponse>("/children", body),
    onSuccess: async (child) => {
      await queryClient.invalidateQueries({ queryKey: qk.me() });
      router.replace(`/child/${child.id}/onboarding`);
    },
  });

  function submit() {
    const next: typeof errors = {};
    if (!nickname.trim()) next.nickname = "부르는 별명을 알려주세요.";
    if (!birthDate) next.birthDate = "생일을 알려주세요.";
    // 나이를 계산하는 게 아니라 입력을 막는 검사다 — 미래에 태어난 아이는 없다.
    else if (birthDate > todayISO()) next.birthDate = "오늘보다 뒤일 수는 없어요.";

    setErrors(next);
    if (Object.keys(next).length > 0) return;

    createChild.mutate({
      nickname: nickname.trim(),
      birth_date: birthDate,
      // 안 고르면 필드를 아예 빼고 보낸다 (프로토타입에서 선택 항목이다).
      ...(relation ? { relation } : {}),
    });
  }

  return (
    <Screen className="gap-6">
      <div>
        <p className="text-label text-ink-subtle">아이 등록 · 1 / 2</p>
        <PageTitle className="mt-2">
          누구 이야기를
          <br />
          모아둘까요
        </PageTitle>
        <p className="text-body text-ink-muted mt-3">
          별명과 생일만 먼저 알려주세요.
          <br />
          나머지는 다음 화면에서 골라도 돼요.
        </p>
      </div>

      <div className="flex flex-col gap-5">
        <TextInput
          label="아이 별명"
          hint="실명이 아니어도 돼요. 화면과 알림에 이렇게 부를게요."
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
          maxLength={20}
          autoComplete="off"
          error={errors.nickname}
        />

        <TextInput
          label="생일"
          hint="나이는 생일을 보고 서버가 계산해요."
          type="date"
          value={birthDate}
          max={todayISO()}
          onChange={(e) => setBirthDate(e.target.value)}
          error={errors.birthDate}
        />

        <div className="flex flex-col gap-1.5">
          <p className="text-label text-ink-muted">부르는 말 · 선택</p>
          <ChipRow>
            {RELATIONS.map((item) => (
              <Chip
                key={item.value}
                selected={relation === item.value}
                onClick={() => setRelation(relation === item.value ? null : item.value)}
              >
                {item.label}
              </Chip>
            ))}
          </ChipRow>
        </div>
      </div>

      <div className="mt-auto flex flex-col gap-3 pt-6">
        {createChild.isError ? (
          <CardFailed>
            {createChild.error instanceof Error
              ? createChild.error.message
              : "아이를 만들지 못했어요."}
          </CardFailed>
        ) : null}

        <Button block onClick={submit} disabled={createChild.isPending}>
          {createChild.isPending ? <Spinner /> : null}
          {createChild.isPending ? "만드는 중…" : "시작하기"}
        </Button>
        <p className="text-caption text-ink-subtle text-center">
          외부 서비스 연결은 요청하지 않아요.
        </p>
      </div>
    </Screen>
  );
}

/** `<input type="date">` 의 상한값. 화면에 나이를 그리는 게 아니라 미래 날짜를 막는 용도다. */
function todayISO(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}
