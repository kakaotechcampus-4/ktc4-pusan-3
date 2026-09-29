"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ConsentChecklist } from "@/components/consent-checklist";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { usePolicies } from "@/hooks/use-policies";
import { api, isApiError, type AuthSession, type AuthSignupRequest } from "@/lib/api";
import {
  clearBind,
  clearConsentCode,
  clearProvider,
  readBind,
  readConsentCode,
  readProvider,
} from "@/lib/auth";
import {
  consentChoices,
  consentPayload,
  POLICY_CHANGED_MESSAGE,
  requiredConsentsChecked,
  toggleConsent,
  type ConsentChecked,
} from "@/lib/consent";
import { useSessionStore } from "@/stores/session";

/**
 * 가입 — 보호자 이름과 계정 동의. **여기를 통과해야 계정이 만들어진다.**
 *
 * 🚨 **무엇을 묻는지는 서버가 정한다** (#90 · `GET /policies`). 화면은 응답에 온 계정 스코프를
 *    순서대로 그리고, 동의에 **응답이 준 버전 그대로**를 실어 보낸다. 상수로 들고 있으면
 *    서버에 등록된 버전과 어긋나 `400 policy_version_invalid` 로 가입이 막히고, 목에는 그
 *    검사가 없어서 화면 작업 중에는 드러나지 않는다 (#90 본문).
 *
 * 🚨 **아이 동의(`child_basic` · `child_health`)는 이 화면에 없다** (#96). 그 둘은 01 아이
 *    만들기 화면이 아이 정보와 **한 트랜잭션**으로 보낸다 — 동의를 아이 단위로 기록하기로
 *    하면서 `child_id` 없이는 저장할 수 없게 됐고, 그 id 는 아이를 만들어야 생긴다.
 *    부수 효과로 **초대로 들어온 보호자와 아이를 등록하는 보호자가 이 화면을 공유한다.**
 *
 * ⚠️ **이름과 동의를 한 화면에 둔다.** 처음에는 화면을 둘로 나눴다 — 법적 고지 화면에 무관한
 *    입력이 같은 제출 버튼에 묶이면 "무엇에 동의한 것인가" 가 흐려진다는 규칙 때문이었다
 *    (docs/web/kakao-login-v1.md §4-5 · 테크스펙 리스크 ④). 합친 이유는 둘이다 —
 *    ① **이름은 무관한 입력이 아니다.** 이 화면이 만드는 것은 보호자 계정이고, 동의 2건이
 *      허락하는 것도 그 계정이다. 아이 정보였다면 갈랐을 것이다 (그래서 실제로 갈라 놨다).
 *    ② 나눠 두니 **화면 하나에 입력칸 하나**만 남아, 가운데가 빈 채로 버튼만 바닥에 붙었다.
 *    🚨 대신 **무엇에 동의하는지는 체크박스가 각자 말한다** — 이름 칸과 동의 구역을 제목으로
 *      가르고, 동의는 항목마다 개별 체크에 전문 보기가 붙는다. 그 구조가 무너지면(예: 이름과
 *      동의를 한 덩어리로 묶거나 "전체 동의" 를 세우면) 합친 것이 그때는 문제가 된다.
 */

/** 아이 별명과 같은 상한. 한쪽만 길면 목록에서 줄이 어긋난다. */
const MAX_NICKNAME = 20;

export default function AuthConsentPage() {
  const router = useRouter();
  const signIn = useSessionStore((s) => s.signIn);
  const hydrated = useSessionStore((s) => s.hydrated);

  const policies = usePolicies();

  const [nickname, setNickname] = useState("");
  const [nicknameError, setNicknameError] = useState<string | null>(null);
  /** 🚨 선택 동의도 기본값은 **꺼짐**이다. 미리 체크해 두면 "고르지 않음" 이 동의가 된다. */
  const [checked, setChecked] = useState<ConsentChecked>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** 가입 대기표가 없으면 이 화면에 올 이유가 없다 (주소 직접 입력 등). */
  const consentCode = useRef<string | null>(null);
  useEffect(() => {
    if (!hydrated) return;
    consentCode.current = readConsentCode();
    if (!consentCode.current) router.replace("/");
  }, [hydrated, router]);

  const choices = consentChoices(policies.data, "account");
  const required = choices.filter(({ policy }) => policy.required);
  const optional = choices.filter(({ policy }) => !policy.required);

  /**
   * 🚨 **막는 것은 `required` 뿐이다.** 이 화면에 선택 동의(`location`)가 서면서 실제로
   *    갈리는 자리가 됐다 — 목록 길이로 세면 선택까지 필수가 된다.
   * 🚨 **목록이 비었으면 제출할 수 없다.** 빈 목록은 "필수가 전부 체크됨" 을 공짜로 통과시킨다
   *    (아무것도 없으니까). 약관을 못 받은 채로 동의 0건을 보내면 서버는 403 으로 막지만,
   *    그 전에 이 화면이 **무엇에 동의하는지 보여주지 않은 것**이 문제다.
   */
  const canSubmit = required.length > 0 && requiredConsentsChecked(choices, checked);

  async function submit() {
    const code = consentCode.current;
    if (!code || !canSubmit) return;

    const name = nickname.trim();
    if (!name) {
      setNicknameError("부르는 이름을 알려주세요.");
      return;
    }

    setPending(true);
    setError(null);
    try {
      const body: AuthSignupRequest = {
        consent_code: code,
        bind: readBind(),
        nickname: name,
        consents: consentPayload(choices, checked),
      };
      const session = await api.post<AuthSession>(`/auth/${readProvider()}/signup`, body);
      signIn(session.token, session.expires_in);

      // 대기표와 bind 는 여기까지가 마지막 쓰임이다.
      clearConsentCode();
      clearBind();
      clearProvider();

      // 🚨 방금 만든 계정이라 아이가 없다. 아이를 만들지 초대를 받을지는 다음 화면이 묻는다 —
      //    여기서 01 로 바로 보내면 초대받은 사람이 같은 아이를 또 등록하게 된다 (#96).
      router.replace("/start");
    } catch (cause) {
      /**
       * 🚨 **다시 시도 버튼을 주지 않는다.** 같은 값을 다시 보내면 또 400 이다. 약관이
       *    바뀐 것이라, 화면을 다시 받아 **바뀐 항목만** 다시 확인받는다 — 체크가 버전을
       *    들고 있어서 바뀐 것만 저절로 풀린다 (`lib/consent.ts` 의 `ConsentChecked`).
       * 🚨 대기표는 **비우지 않는다.** 서버가 소비 전에 막았으므로 살아 있다 (§3-5).
       */
      if (isApiError(cause, "policy_version_invalid")) {
        await policies.refetch();
        setError(POLICY_CHANGED_MESSAGE);
        return;
      }
      setError(cause instanceof Error ? cause.message : "동의를 저장하지 못했어요.");
    } finally {
      setPending(false);
    }
  }

  return (
    <Screen className="gap-6">
      <div>
        <p className="text-label text-ink-subtle">가입</p>
        <PageTitle className="mt-2">
          시작하기 전에
          <br />
          확인해 주세요
        </PageTitle>
        <p className="text-body text-ink-muted mt-3">
          이름 하나와 보호자 계정에 대한 동의예요. 아이에 대한 동의는 아이를 등록할 때 따로 받아요.
        </p>
      </div>

      <TextInput
        label="내 이름"
        hint="아이 이름이 아니라 보호자 이름이에요. 실명이 아니어도 돼요."
        value={nickname}
        onChange={(e) => {
          setNickname(e.target.value);
          if (nicknameError) setNicknameError(null);
        }}
        maxLength={MAX_NICKNAME}
        autoComplete="off"
        error={nicknameError}
      />

      <div className="flex flex-col gap-3">
        {/* 🚨 이름 칸과 **제목으로 가른다.** 같은 제출 버튼을 쓰더라도 무엇에 동의하는지는
            아래 항목들이 각자 말해야 한다 (위 머리말 ⚠️). */}
        <p className="text-section text-ink">보호자 계정에 대한 동의</p>

        {policies.isPending ? (
          <Card>
            <p className="text-body text-ink-muted flex items-center gap-2">
              <Spinner />
              동의 항목을 불러오는 중…
            </p>
          </Card>
        ) : null}

        {/* 🚨 **기본값으로 그리지 않는다.** 약관을 못 받았으면 무엇에 동의하는지 모르는
            것이고, 그 상태로 체크박스를 세우면 화면이 지어낸 것에 동의를 받는 셈이다. */}
        {!policies.isPending && required.length === 0 ? (
          <>
            <CardFailed>
              <p>동의 항목을 불러오지 못했어요.</p>
              <p className="mt-1">잠시 뒤에 다시 불러와 주세요. 로그인은 그대로예요.</p>
            </CardFailed>
            <Button variant="secondary" onClick={() => void policies.refetch()}>
              다시 불러오기
            </Button>
          </>
        ) : null}

        {required.length > 0 ? (
          <>
            {/* 🚨 **필수와 선택을 한 무리로 그리지 않는다** (디자인 시스템 §7 동의 목록).
                같은 체크박스가 죽 늘어서 있으면 선택도 채워야 넘어가는 칸으로 읽히고,
                반대로 필수가 골라도 되는 것처럼 읽힌다. 머리줄로 가른다. */}
            <p className="text-label text-ink-muted">필수</p>
            <ConsentChecklist
              choices={required}
              checked={checked}
              onChange={(policy, next) => setChecked((prev) => toggleConsent(prev, policy, next))}
            />
          </>
        ) : null}

        {optional.length > 0 ? (
          <>
            <p className="text-label text-ink-muted mt-2">선택</p>
            <ConsentChecklist
              choices={optional}
              checked={checked}
              onChange={(policy, next) => setChecked((prev) => toggleConsent(prev, policy, next))}
            />
            {/* 🚨 "지금 안 해도 된다" 를 **고르기 전에** 말한다. 선택 동의를 가입 화면에
                올리는 대가가 "필수처럼 보이는 것" 이라, 그 대가를 여기서 갚는다. */}
            <p className="text-caption text-ink-subtle">
              지금 켜지 않아도 돼요. 설정에서 언제든 켜고 끌 수 있어요.
            </p>
          </>
        ) : null}
      </div>

      {/* 🚨 **바닥에 붙이지 않는다** (디자인 시스템 §5). `mt-auto` 는 남는 높이를 **본문과
          버튼 사이** 한 곳으로 몬다 — 폰에서는 내용이 이미 넘쳐서 아무 일도 안 하지만
          (390×844 에서 문서 1138px), 뷰포트가 내용보다 길어지는 순간 그 빈칸이 화면에서
          제일 큰 간격이 된다 (1280×1400 에서 354px). 규칙의 전제가 "스크롤이 생기는 폼" 이라,
          전제가 깨지는 높이에서는 규칙도 같이 깨진다.
          🚨 버튼은 **내용 바로 뒤**를 따라가고 화면은 그냥 끝난다. 아래가 비는 것과
          가운데가 비는 것은 다르게 읽힌다 (05 제안 후보 · 00-1 · 초대 코드와 같은 처리). */}
      <div className="flex flex-col gap-3 pt-2">
        {error ? <CardFailed>{error}</CardFailed> : null}

        <Button block onClick={submit} disabled={!canSubmit || pending}>
          {pending ? <Spinner /> : null}
          {pending ? "저장하는 중…" : "동의하고 시작하기"}
        </Button>
        <p className="text-caption text-ink-subtle text-center">
          필수에 동의하지 않으면 계정이 만들어지지 않아요.
        </p>
      </div>
    </Screen>
  );
}
