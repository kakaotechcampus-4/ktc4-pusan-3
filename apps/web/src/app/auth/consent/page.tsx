"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import {
  api,
  type AuthSession,
  type ConsentRequest,
  type ConsentResponse,
  type AuthSignupRequest,
} from "@/lib/api";
import {
  clearBind,
  clearConsentCode,
  clearProvider,
  readBind,
  readConsentCode,
  readProvider,
} from "@/lib/auth";
import {
  CONSENT_POLICY_VERSION,
  SIGNUP_CONSENTS,
  TERMS_NOT_FINAL,
  type ConsentItem,
  type ConsentScope,
} from "@/lib/consent";
import { useSessionStore } from "@/stores/session";

/**
 * 가입 동의 — 신규 회원만 지나는 화면이다. **여기를 통과해야 계정이 만들어진다.**
 *
 * 🚨 이 화면에 다른 입력을 섞지 않는다 (docs/web/kakao-login-v1.md §4-5).
 *    법적 고지를 읽고 확인하는 화면인데 무관한 입력이 같은 제출 버튼에 묶이면
 *    "무엇에 동의한 것인가" 가 흐려진다. 보호자 닉네임을 여기서 받지 않는 것도 같은 이유다.
 *
 * 🚨 "전체 동의" 를 두지 않는다. 민감정보(child_health)는 다른 동의와 **구분해서** 받아야
 *    한다 (개인정보보호법 제23조). 한 번에 쓸어 담는 버튼이 그 구분을 없앤다.
 *
 * 🚨 승인 게이트가 아니다 — `btn-approve` · `caution` 색을 쓰지 않는다.
 *    그 둘은 되돌릴 수 없는 2곳(캘린더 쓰기 · 건강 기록 확정) 전용이다 (CLAUDE.md §2).
 */

/** 계정이 만들어진 뒤 아이 스코프가 실패하면 여기부터 다시 한다. */
type Stage = "consent" | "child_scopes";

export default function AuthConsentPage() {
  const router = useRouter();
  const signIn = useSessionStore((s) => s.signIn);
  const hydrated = useSessionStore((s) => s.hydrated);

  const [checked, setChecked] = useState<Record<ConsentScope, boolean>>({
    service_terms: false,
    privacy_account: false,
    child_basic: false,
    child_health: false,
  });
  const [stage, setStage] = useState<Stage>("consent");
  /** 상세를 펼쳐 볼 항목. null 이면 시트가 닫혀 있다. */
  const [detail, setDetail] = useState<ConsentItem | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** 가입 대기표가 없으면 이 화면에 올 이유가 없다 (주소 직접 입력 등). */
  const consentCode = useRef<string | null>(null);
  useEffect(() => {
    if (!hydrated) return;
    consentCode.current = readConsentCode();
    if (!consentCode.current) router.replace("/");
  }, [hydrated, router]);

  const allChecked = SIGNUP_CONSENTS.every((item) => checked[item.scope]);

  async function submit() {
    const code = consentCode.current;
    if (!code || !allChecked) return;

    setPending(true);
    setError(null);
    try {
      // ① 계정 스코프 → 계정이 여기서 만들어진다 (parent · auth_identity · consent 한 트랜잭션).
      if (stage === "consent") {
        const body: AuthSignupRequest = {
          consent_code: code,
          bind: readBind(),
          consents: SIGNUP_CONSENTS.filter((i) => i.target === "account").map((i) => ({
            scope: i.scope,
            policy_version: CONSENT_POLICY_VERSION,
          })),
        };
        const session = await api.post<AuthSession>(`/auth/${readProvider()}/signup`, body);
        signIn(session.token, session.expires_in);
        // 대기표는 다 썼다. bind 도 여기까지가 마지막 쓰임이다.
        clearConsentCode();
        clearBind();
        clearProvider();
        setStage("child_scopes");
      }

      // ② 아이 스코프 → 🚨 아이를 만들기 **전에** 받아야 한다.
      //    child_basic 없이 POST /children 은 403 이다 (계약서 §04 "동의는 저장보다 먼저다").
      //    아직 아이가 없으므로 child_id 를 싣지 않는다 (#18 확인 대상).
      for (const item of SIGNUP_CONSENTS.filter((i) => i.target === "child")) {
        const body: ConsentRequest = {
          scope: item.scope,
          action: "granted",
          policy_version: CONSENT_POLICY_VERSION,
          guardian_attested: true,
        };
        await api.post<ConsentResponse>("/consents", body);
      }

      // 방금 만든 계정이라 아이가 없다. /me 를 물어볼 것도 없이 01 화면으로 간다.
      router.replace("/onboarding");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "동의를 저장하지 못했어요.");
    } finally {
      setPending(false);
    }
  }

  return (
    <Screen className="gap-6">
      <div>
        <PageTitle>
          시작하기 전에
          <br />
          확인해 주세요
        </PageTitle>
        <p className="text-body text-ink-muted mt-3">
          네 가지 모두 동의해야 아이 기억을 만들 수 있어요.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        {SIGNUP_CONSENTS.map((item) => (
          <Card key={item.scope}>
            <Checkbox
              checked={checked[item.scope]}
              onChange={(next) => setChecked((prev) => ({ ...prev, [item.scope]: next }))}
              label={
                <>
                  <span className="text-ink-muted">[필수] </span>
                  {item.label}
                </>
              }
              description={item.description}
            />
            {item.legalBasis ? (
              <p className="text-caption text-ink-subtle mt-1 pl-8">{item.legalBasis}</p>
            ) : null}
            <div className="pl-6">
              <Button variant="tertiary" onClick={() => setDetail(item)}>
                상세 보기
              </Button>
            </div>
          </Card>
        ))}
      </div>

      <BottomSheet
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={detail?.label ?? ""}
        description={detail?.legalBasis}
        footer={
          <Button block onClick={() => setDetail(null)}>
            닫기
          </Button>
        }
      >
        {detail ? <ConsentDetail item={detail} /> : null}
      </BottomSheet>

      <div className="mt-auto flex flex-col gap-3 pt-2">
        {error ? (
          <CardFailed>
            <p>{error}</p>
            {stage === "child_scopes" ? (
              <p className="mt-1">계정은 이미 만들어졌어요. 다시 시도하면 남은 동의만 저장해요.</p>
            ) : null}
          </CardFailed>
        ) : null}

        <Button block onClick={submit} disabled={!allChecked || pending}>
          {pending ? <Spinner /> : null}
          {pending ? "저장하는 중…" : "동의하고 시작하기"}
        </Button>
        <p className="text-caption text-ink-subtle text-center">
          동의하지 않으면 계정이 만들어지지 않아요.
        </p>
      </div>
    </Screen>
  );
}

/**
 * 🚨 약관 전문이 아니다. 보관 기간·삭제 범위가 아직 미정이라(CLAUDE.md §10) 정식 문구를
 *    쓸 수 없고, 없는 조항을 지어 넣으면 그대로 배포된다. 확정된 사실만 보여주고
 *    아직 최종본이 아니라는 것을 화면에 밝힌다.
 */
function ConsentDetail({ item }: { item: ConsentItem }) {
  return (
    <div className="flex flex-col gap-5">
      {item.details.map((section) => (
        <section key={section.heading}>
          <h3 className="text-section text-ink">{section.heading}</h3>
          <ul className="text-body-sm text-ink-muted marker:text-ink-subtle mt-2 flex list-disc flex-col gap-1.5 pl-5">
            {section.lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
      ))}
      <p className="text-caption text-ink-subtle border-line border-t pt-4">{TERMS_NOT_FINAL}</p>
    </div>
  );
}
