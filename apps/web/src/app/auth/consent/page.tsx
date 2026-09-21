"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ConsentChecklist, requiredConsentsChecked } from "@/components/consent-checklist";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { api, type AuthSession, type AuthSignupRequest } from "@/lib/api";
import {
  clearBind,
  clearConsentCode,
  clearProvider,
  readBind,
  readConsentCode,
  readProvider,
} from "@/lib/auth";
import { ACCOUNT_SIGNUP_CONSENTS, CONSENT_POLICY_VERSION, type ConsentScope } from "@/lib/consent";
import { useSessionStore } from "@/stores/session";

/**
 * 가입 — 보호자 이름과 계정 동의. **여기를 통과해야 계정이 만들어진다.**
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
 *      가르고, 동의는 항목마다 개별 체크에 전문 시트가 붙는다. 그 구조가 무너지면(예: 이름과
 *      동의를 한 덩어리로 묶거나 "전체 동의" 를 세우면) 합친 것이 그때는 문제가 된다.
 */

/** 아이 별명과 같은 상한. 한쪽만 길면 목록에서 줄이 어긋난다. */
const MAX_NICKNAME = 20;

export default function AuthConsentPage() {
  const router = useRouter();
  const signIn = useSessionStore((s) => s.signIn);
  const hydrated = useSessionStore((s) => s.hydrated);

  const [nickname, setNickname] = useState("");
  const [nicknameError, setNicknameError] = useState<string | null>(null);
  /** 🚨 선택 동의도 기본값은 **꺼짐**이다. 미리 체크해 두면 "고르지 않음" 이 동의가 된다. */
  const [checked, setChecked] = useState<Partial<Record<ConsentScope, boolean>>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** 가입 대기표가 없으면 이 화면에 올 이유가 없다 (주소 직접 입력 등). */
  const consentCode = useRef<string | null>(null);
  useEffect(() => {
    if (!hydrated) return;
    consentCode.current = readConsentCode();
    if (!consentCode.current) router.replace("/");
  }, [hydrated, router]);

  const requiredChecked = requiredConsentsChecked(ACCOUNT_SIGNUP_CONSENTS, checked);

  async function submit() {
    const code = consentCode.current;
    if (!code || !requiredChecked) return;

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
        // 🚨 **고른 것만 보낸다.** 선택을 안 고른 스코프까지 실어 보내면 화면이 물어본
        //    것과 서버에 남는 것이 달라진다.
        consents: ACCOUNT_SIGNUP_CONSENTS.filter((i) => checked[i.scope] === true).map((i) => ({
          scope: i.scope,
          policy_version: CONSENT_POLICY_VERSION,
        })),
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
          이름 하나와 보호자 계정에 대한 동의 두 가지예요. 아이에 대한 동의는 아이를 등록할 때 따로
          받아요.
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
            이 아래 두 항목이 각자 말해야 한다 (위 머리말 ⚠️). */}
        <p className="text-section text-ink">보호자 계정에 대한 동의</p>
        <ConsentChecklist
          items={ACCOUNT_SIGNUP_CONSENTS}
          checked={checked}
          onChange={(scope, next) => setChecked((prev) => ({ ...prev, [scope]: next }))}
        />
      </div>

      <div className="mt-auto flex flex-col gap-3 pt-2">
        {error ? <CardFailed>{error}</CardFailed> : null}

        <Button block onClick={submit} disabled={!requiredChecked || pending}>
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
