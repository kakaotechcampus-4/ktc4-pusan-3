"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ConsentChecklist } from "@/components/consent-checklist";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { DateField } from "@/components/ui/date-field";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { usePolicies } from "@/hooks/use-policies";
import { api, isApiError, qk, type CreateChildRequest, type CreateChildResponse } from "@/lib/api";
import {
  consentChoices,
  consentPayload,
  POLICY_CHANGED_MESSAGE,
  requiredConsentsChecked,
  toggleConsent,
  type ConsentChecked,
} from "@/lib/consent";
import { toISODate } from "@/lib/format";

/**
 * 01 첫 진입 — 아이 만들기.
 *
 * 🚨 프로토타입은 **나이**를 드롭다운으로 받지만 여기서는 **생일**을 받는다.
 *    계약서가 `birth_date` 를 받고, 나이 → 생일 환산은 프론트가 날짜를 계산하는 것이라 금지다
 *    (apps/web/CLAUDE.md §4). 나이 문구(`age_display`)는 서버가 만들어 내려준다.
 *
 * 🚨 수집은 별명 · 생일까지다. 프로필 질문을 늘리지 않는다 (CLAUDE.md §2 개인정보).
 *
 * 🚨 **고를 수 있는 것은 이 화면에 두지 않는다.** 관계 · 성별 · 키 · 몸무게 · 알레르기는
 *    전부 02 가 받는다. 이 화면이 받는 것은 **되돌리기 어려운 것**뿐이다 — 아이를 만드는
 *    일과 그 아이 정보에 대한 법정대리인 동의. 선택 항목을 같은 버튼에 묶어 두면 "지금
 *    꼭 정해야 하는 것" 과 "나중에 해도 되는 것" 이 한 덩어리로 보인다.
 *
 * 🚨 **아이 동의 2건과 법정대리인 확인을 이 화면이 받는다** (#96). 가입 동의 화면이 아니라
 *    여기인 이유는 두 가지다 —
 *    ① 동의를 **아이 단위**로 기록하기로 하면서 `child_id` 없이는 저장할 수 없게 됐다.
 *      그 id 는 이 호출이 만든다. 그래서 동의는 아이와 **한 트랜잭션**으로 간다.
 *    ② 가입 화면에서 받으면, 가입만 끝내고 이 화면에서 나간 사람의 동의가 갈 곳 없이
 *      사라진다. 값이 쓰이는 화면에 두면 이탈해도 늘 같은 자리에 있다.
 *    🚨 초대로 들어온 보호자는 이 화면을 지나지 않는다 — 그 아이의 법정대리인 동의는
 *      이미 받았고, 법정대리인이 아닌 사람에게 또 받으면 그 동의가 무효다.
 */

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

  const policies = usePolicies();
  const choices = consentChoices(policies.data, "child");

  const [nickname, setNickname] = useState("");
  const [birthDate, setBirthDate] = useState("");
  /** 🚨 기본값은 **꺼짐**이다. 미리 체크해 두면 "고르지 않음" 이 동의가 된다. */
  const [consents, setConsents] = useState<ConsentChecked>({});
  const [attested, setAttested] = useState(false);
  const [errors, setErrors] = useState<{ nickname?: string; birthDate?: string }>({});
  /** 약관이 바뀌어 다시 확인받아야 할 때만 찬다 (`policy_version_invalid`). */
  const [policyChanged, setPolicyChanged] = useState(false);

  const createChild = useMutation({
    mutationFn: (body: CreateChildRequest) => api.post<CreateChildResponse>("/children", body),
    onSuccess: async (child) => {
      await queryClient.invalidateQueries({ queryKey: qk.me() });
      router.replace(`/child/${child.id}/onboarding`);
    },
    /**
     * 🚨 가입 동의 화면과 **같은 처리**다 — 약관이 바뀐 것이지 고장이 아니라서, 다시 받아
     *    **바뀐 항목만** 다시 확인받는다 (체크가 버전을 들고 있어 저절로 풀린다).
     *    아이는 아직 만들어지지 않았다 — 서버가 저장 전에 막는다.
     */
    onError: async (cause) => {
      if (!isApiError(cause, "policy_version_invalid")) return;
      setPolicyChanged(true);
      await policies.refetch();
    },
  });

  /**
   * 🚨 **목록이 비었으면 만들 수 없다.** 빈 목록은 "필수가 전부 체크됨" 을 공짜로 통과시킨다 —
   *    약관을 못 받은 채로 아이를 만들면 동의 없이 아이가 생기거나(그건 사고다) 403 이다.
   */
  const consentsReady =
    choices.length > 0 && requiredConsentsChecked(choices, consents) && attested;

  function submit() {
    const next: typeof errors = {};
    if (!nickname.trim()) next.nickname = "부르는 별명을 알려주세요.";
    if (!birthDate) next.birthDate = "생일을 알려주세요.";
    // 나이를 계산하는 게 아니라 입력을 막는 검사다 — 미래에 태어난 아이는 없다.
    // 달력이 이미 막지만, 값이 다른 경로로 들어올 수 있어 제출에서도 본다.
    else if (birthDate > toISODate(today())) next.birthDate = "오늘보다 뒤일 수는 없어요.";

    setErrors(next);
    if (Object.keys(next).length > 0 || !consentsReady) return;

    setPolicyChanged(false);
    createChild.mutate({
      nickname: nickname.trim(),
      birth_date: birthDate,
      // 🚨 **고른 것만, 화면이 그린 버전 그대로 보낸다** (`GET /policies`). 상수로 들고 있으면
      //    서버에 등록된 버전과 어긋나 400 이고, 화면이 물어본 것과 서버에 남는 것도 달라진다.
      consents: consentPayload(choices, consents),
      // 🚨 상수 true 를 보내지 않는다. 체크박스 값 그대로다 — 아무도 확인하지 않은 동의가
      //    확인된 것으로 남으면 그 동의는 증빙이 아니다.
      guardian_attested: attested,
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
          나머지는 다음 화면에서 골라도 되고, 건너뛰어도 돼요.
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

        <DateField
          placeholder="생일을 골라주세요"
          label="생일"
          hint="나이는 생일을 보고 서버가 계산해요."
          value={birthDate}
          onChange={setBirthDate}
          error={errors.birthDate}
          fromDate={EARLIEST_BIRTH_DATE}
          toDate={today()}
        />
      </div>

      <div className="flex flex-col gap-3">
        <div>
          <p className="text-section text-ink">아이 정보에 대한 동의</p>
          <p className="text-body-sm text-ink-muted mt-1">
            아이를 등록하는 것과 같이 저장돼요. 동의하지 않으면 등록되지 않아요.
          </p>
        </div>

        {policies.isPending ? (
          <Card>
            <p className="text-body text-ink-muted flex items-center gap-2">
              <Spinner />
              동의 항목을 불러오는 중…
            </p>
          </Card>
        ) : null}

        {/* 🚨 **기본값으로 그리지 않는다.** 약관을 못 받았으면 무엇에 동의하는지 모르는 것이고,
            그 상태로 아이를 만들면 근거 없는 동의가 아이와 함께 저장된다. */}
        {!policies.isPending && choices.length === 0 ? (
          <>
            <CardFailed>
              <p>동의 항목을 불러오지 못했어요.</p>
              <p className="mt-1">잠시 뒤에 다시 불러와 주세요. 적으신 것은 그대로예요.</p>
            </CardFailed>
            <Button variant="secondary" onClick={() => void policies.refetch()}>
              다시 불러오기
            </Button>
          </>
        ) : null}

        <ConsentChecklist
          choices={choices}
          checked={consents}
          onChange={(policy, next) => setConsents((prev) => toggleConsent(prev, policy, next))}
        />

        {/*
          🚨 **동의 항목이 아니라 그 동의의 유효 요건이다** (개인정보보호법 제22조의2).
             그래서 `ConsentChecklist` 안이 아니라 밖에 서고, 전문 시트도 없다 —
             읽고 확인하는 것이 아니라 **본인이 누구인지 밝히는 것**이다.
          🚨 **만 19세 표시를 겸한다** (약관 제5조 ④ · 제6조 ② · #166). 아이를 등록하는 사람은
             따로 묻지 않고 이 문항 하나로 받기로 했다 — 그래서 문구에서 "만 19세 이상" 을 빼면
             등록 보호자에게는 19세 표시를 받은 적이 없게 된다. 약관 문장과 같은 두 가지를 담는다.
             초대받은 보호자는 법정대리인 표시 없이 나이만 표시한다 (`app/invite/page.tsx`).
        */}
        <Card>
          <Checkbox
            checked={attested}
            onChange={setAttested}
            label={
              <>
                <span className="text-ink-muted">[필수] </span>
                내가 이 아이의 법정대리인이고, 만 19세 이상이에요
              </>
            }
            description="위 두 가지 동의는 법정대리인만 할 수 있어요. 함께 보는 보호자는 초대로 연결하면 돼요."
          />
        </Card>
      </div>

      {/* 🚨 **바닥에 붙이지 않는다** — 가입 동의 화면과 같은 이유다 (디자인 시스템 §5).
          폰에서는 내용이 넘쳐서 `mt-auto` 가 무의미하고(390×844 에서 문서 1123px), 뷰포트가
          길어지면 본문과 버튼 사이만 벌어진다(1280×1400 에서 335px). 버튼은 내용 바로 뒤다. */}
      <div className="flex flex-col gap-3 pt-6">
        {createChild.isError ? (
          <CardFailed>
            {/* 🚨 약관이 바뀐 400 은 "만들지 못했어요" 가 아니다 — 무엇을 해야 하는지가 다르다
                (다시 시도가 아니라 **다시 확인**이다). */}
            {policyChanged
              ? POLICY_CHANGED_MESSAGE
              : createChild.error instanceof Error
                ? createChild.error.message
                : "아이를 만들지 못했어요."}
          </CardFailed>
        ) : null}

        <Button block onClick={submit} disabled={!consentsReady || createChild.isPending}>
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

/** 달력의 상한. 화면에 나이를 그리는 게 아니라 미래 날짜를 막는 용도다. */
function today(): Date {
  return new Date();
}

/**
 * 달력의 하한. 아이 서비스라 20년 전이면 충분하고, 연도 드롭다운이 무한정 길어지지 않는다.
 * 🚨 나이 계산이 아니다 — 고를 수 있는 범위를 정하는 것뿐이다.
 */
const EARLIEST_BIRTH_DATE = new Date(new Date().getFullYear() - 20, 0, 1);
