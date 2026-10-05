"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Chip, ChipRow } from "@/components/ui/chip";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import {
  api,
  isApiError,
  qk,
  type InviteAcceptRequest,
  type InviteAcceptResponse,
  type InvitePreviewResponse,
  type Relation,
} from "@/lib/api";
import {
  INVITE_CODE_LENGTH,
  formatInviteCode,
  isInviteCodeComplete,
  normalizeInviteCode,
} from "@/lib/invite-code";
import { MEMBER_RELATIONS, relationOptions } from "@/lib/relation";

/**
 * 초대 코드로 참여. **아이가 0명인 계정이 00-1 에서 들어온다.**
 *
 * 🚨 **두 단계다. 코드를 넣자마자 연결하지 않는다.** ㉠ 코드 확인 → ㉡ 어느 아이인지 보고
 *    확정. 코드는 카톡으로 오가는 여덟 글자라 **엉뚱한 아이에 붙을 수 있다** — 잘못 받아
 *    적었거나, 다른 사람에게 갈 코드를 받았거나. 붙고 나면 되돌리는 길이
 *    "설정에서 연결 끊기" 뿐이고, 그 사이에 그 아이의 기록과 건강 정보를 다 본다.
 *    **되돌릴 수 없는 것은 아니지만, 보고 나서 되돌릴 수는 없다.**
 *
 * 🚨 **확인 단계를 승인 게이트로 그리지 않는다** — `caution` · `btn-approve` 를 쓰지 않는다.
 *    게이트는 딱 2곳(캘린더 쓰기 · 건강 기록 확정)이고 늘리지 않는다 (최상위 §2).
 *    여기는 "무엇에 연결되는지 먼저 보여준다" 이지 승인이 아니다.
 *
 * 🚨 **여기서 아이 동의를 받지 않는다** (#96). 그 아이에 대한 법정대리인 동의는 아이를
 *    등록한 보호자가 이미 했다 — 법정대리인이 아닌 사람에게 또 받으면 그 동의가 무효다.
 *
 * 🚨 **대신 만 19세 이상 표시를 받는다** (약관 제5조 ④ · 제7조 ② · #166). 나이를 묻는 이유가
 *    아이 정보 보호라서 **아이 정보에 닿기 직전**인 이 화면이 묻는다 — 가입만 한 사람은
 *    아이 정보가 없다. 아이를 등록하는 사람은 01 의 법정대리인 문항이 같은 표시를 겸한다.
 *
 * 🚨 **로그인 뒤에 입력한다.** 링크를 먼저 열고 로그인하는 흐름이 아니다 — 카카오 왕복을
 *    건너는 동안 `sessionStorage` 가 살아 있다는 보장이 없어서, 초대 토큰만이 아니라
 *    `bind` 까지 잃으면 로그인 자체가 깨진다 (`lib/invite-code.ts` 머리말).
 *    그래서 두 호출 다 `Bearer` 를 요구하는 평범한 호출이다 — 무인증 엔드포인트가 늘지 않는다.
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

const DEFAULT_ERROR_MESSAGE = "초대를 확인하지 못했어요. 잠시 후 다시 시도해 주세요.";

function message(error: unknown): string {
  if (isApiError(error)) return ERROR_MESSAGE[error.code] ?? DEFAULT_ERROR_MESSAGE;
  return DEFAULT_ERROR_MESSAGE;
}

function InviteScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();

  /** 🚨 정본은 **정규화된 값**이다. 화면에만 네 자씩 끊어 보여준다. */
  const [code, setCode] = useState("");
  /**
   * 확인된 초대. 있으면 2단계다.
   * 🚨 `useQuery` 가 아니다 — 버튼을 눌러야 조회하는 것이고, 코드를 고칠 때마다 다시
   *    부르면 **틀린 코드로 시도 제한을 스스로 소모한다** (`429`).
   * 🚨 **조회한 코드를 미리보기와 함께 들고 있고, 수락은 그 코드로 한다** (#150 멘토 리뷰).
   *    입력창의 `code` 로 수락하면 A 로 확인하는 사이 B 로 고쳤을 때 **A 의 아이를 보고 B 의
   *    아이에 연결된다.** 보여 준 것과 연결되는 것이 같다는 보장은 이 한 쌍이 진다.
   */
  const [preview, setPreview] = useState<{ code: string; invite: InvitePreviewResponse } | null>(
    null,
  );
  const [relation, setRelation] = useState<Relation | null>(null);
  const [adultAttested, setAdultAttested] = useState(false);

  const check = useMutation({
    mutationFn: (value: string) =>
      api.get<InvitePreviewResponse>(`/invites/${encodeURIComponent(value)}`),
    // 🚨 `variables` 가 조회에 쓴 코드다 — 응답이 온 시점의 입력창 값이 아니다.
    onSuccess: (invite, value) => setPreview({ code: value, invite }),
  });

  const accept = useMutation({
    mutationFn: ({ code: confirmed, body }: { code: string; body: InviteAcceptRequest }) =>
      api.post<InviteAcceptResponse>(`/invites/${encodeURIComponent(confirmed)}/accept`, body),
    onSuccess: async (child) => {
      await queryClient.invalidateQueries({ queryKey: qk.me() });
      router.replace(`/child/${child.child_id}/home`);
    },
  });

  function backToCode() {
    setPreview(null);
    setRelation(null);
    // 🚨 다른 코드로 돌아가면 표시도 처음부터다 — 앞 아이에 대해 한 표시가 다음 아이로 넘어가지 않게.
    setAdultAttested(false);
    accept.reset();
    check.reset();
  }

  if (preview) {
    const { invite } = preview;
    return (
      <Screen className="gap-6">
        <div>
          <p className="text-label text-ink-subtle">초대 확인</p>
          <PageTitle className="mt-2">
            이 아이가
            <br />
            맞나요
          </PageTitle>
          <p className="text-body text-ink-muted mt-3">
            맞다면 연결할게요. 아니면 코드를 다시 확인해 주세요.
          </p>
        </div>

        {/* 🚨 **별명과 나이까지다.** 아직 연결되지 않은 사람이라 건강·알레르기를 여기
            보여주지 않는다 (`InvitePreviewResponse` 주석 · 최상위 §2 개인정보). */}
        <Card tone="accent">
          <p className="text-title text-ink">{invite.child.nickname}</p>
          <p className="text-body-sm text-ink-muted mt-1">{invite.child.age_display}</p>
          {invite.invited_by.nickname ? (
            <p className="text-body-sm text-ink-muted border-line mt-3 border-t pt-3">
              {invite.invited_by.nickname} 님이 초대했어요
            </p>
          ) : null}
        </Card>

        <div className="flex flex-col gap-1.5">
          {/* 🚨 **관계는 받는 쪽이 고른다** (#89). 초대를 발행할 때 지정하지 않는다.
              🚨 여기는 5종 전부다 — 이 사람은 법정대리인 동의를 하지 않으므로 시터도
              자기 관계를 그대로 말할 수 있다 (`lib/relation.ts`). */}
          <p className="text-label text-ink-muted">아이와의 관계 · 선택</p>
          <ChipRow>
            {relationOptions(MEMBER_RELATIONS).map((item) => (
              <Chip
                key={item.value}
                selected={relation === item.value}
                onClick={() => setRelation(relation === item.value ? null : item.value)}
              >
                {item.label}
              </Chip>
            ))}
          </ChipRow>
          {/* 🚨 **연결하기 전에 알린다** (약관 제7조 ④ · 동의문 1 v2). 같은 아이의 보호자끼리는
              부르는 이름과 관계가 서로 보인다 — 고르는 자리 바로 아래에 둬야 "이 값이 남에게
              보인다" 가 고르기 전에 읽힌다. 관계를 안 골라도 이름은 보이므로 늘 띄운다. */}
          <p className="text-caption text-ink-subtle mt-1">
            이 아이의 다른 보호자에게 내 부르는 이름과 아이와의 관계가 보여요.
          </p>
        </div>

        <Card>
          <p className="text-body-sm text-ink">연결하면 이런 것을 함께 보게 돼요</p>
          <ul className="text-body-sm text-ink-muted marker:text-ink-subtle mt-2 flex list-disc flex-col gap-1.5 pl-5">
            <li>아이의 기록과 쌓인 기억</li>
            <li>보호자가 입력한 알레르기 · 건강 정보</li>
            <li>등록된 일정과 준비물</li>
          </ul>
          <p className="text-caption text-ink-subtle mt-3">
            아이를 등록한 보호자만 할 수 있는 일이 따로 있고, 연결은 설정에서 끊을 수 있어요.
          </p>
        </Card>

        {/* 🚨 **동의 항목이 아니라 본인이 누구인지 밝히는 표시다** — 01 의 법정대리인 문항과
            같은 자리에 같은 모양으로 선다. 전문 시트가 없다.
            🚨 `guardian_attested` 가 아니다. 이 사람은 법정대리인이 아닐 수 있어서 그 표시를 받지
            않는다 (약관 제7조 ②). 문구에 "법정대리인" 을 넣지 않는다. */}
        <Card>
          <Checkbox
            checked={adultAttested}
            onChange={setAdultAttested}
            label={
              <>
                <span className="text-ink-muted">[필수] </span>만 19세 이상이에요
              </>
            }
            description="아이의 기록과 건강 정보를 함께 보게 돼서 성인만 연결할 수 있어요."
          />
        </Card>

        <div className="flex flex-col gap-3">
          {accept.isError ? <CardFailed>{message(accept.error)}</CardFailed> : null}

          <Button
            block
            onClick={() =>
              accept.mutate({
                code: preview.code,
                // 🚨 상수 true 가 아니다. 체크박스 값 그대로 싣는다 (`InviteAcceptRequest`).
                body: { ...(relation ? { relation } : {}), adult_attested: adultAttested },
              })
            }
            disabled={!adultAttested || accept.isPending}
          >
            {accept.isPending ? <Spinner /> : null}
            {accept.isPending ? "연결하는 중…" : "연결하기"}
          </Button>
          <button
            type="button"
            className="text-button text-ink-muted min-h-touch"
            onClick={backToCode}
          >
            코드를 다시 입력할게요
          </button>
        </div>
      </Screen>
    );
  }

  const ready = isInviteCodeComplete(code);

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
        // 🚨 확인하는 동안 코드를 못 바꾼다 (#150 멘토 리뷰). 수락은 이미 조회한 코드로 나가지만,
        //    응답이 오면 입력창과 다른 코드의 아이가 뜨는 화면 자체를 만들지 않는다.
        //    `disabled` 가 아니라 `readOnly` 다 — 모바일에서 포커스와 키보드가 날아가지 않는다.
        readOnly={check.isPending}
        onChange={(e) => {
          setCode(normalizeInviteCode(e.target.value));
          if (check.isError) check.reset();
        }}
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        inputMode="text"
        className="text-center tracking-[0.2em] uppercase"
      />

      {/* 🚨 바닥에 붙이지 않는다. 내용이 둘뿐인 화면에서 `mt-auto` 는 입력칸과 버튼 사이에
          빈 화면을 한 폭 만든다 (디자인 시스템 §5 "바닥에 붙이는 것"). */}
      <div className="flex flex-col gap-3">
        {check.isError ? <CardFailed>{message(check.error)}</CardFailed> : null}

        <Button block onClick={() => check.mutate(code)} disabled={!ready || check.isPending}>
          {check.isPending ? <Spinner /> : null}
          {check.isPending ? "확인하는 중…" : "코드 확인하기"}
        </Button>
        {/* 🚨 여기서 아직 아무것도 연결되지 않는다는 것을 적는다 — 버튼 이름만으로는
            "확인" 이 곧 연결로 읽힌다. */}
        <p className="text-caption text-ink-subtle text-center">
          누구의 초대인지 먼저 보여드려요. 아직 연결되지 않아요.
        </p>
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
