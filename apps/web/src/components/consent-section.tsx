"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, MapPin, ShieldCheck, UserRound } from "lucide-react";
import { useState } from "react";

import { SettingsGroup, SettingsInfoRow } from "@/components/settings-row";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  api,
  qk,
  type ConsentRequest,
  type ConsentResponse,
  type ConsentsResponse,
} from "@/lib/api";
import {
  CONSENT_POLICY_VERSION,
  OPTIONAL_CONSENTS,
  REQUIRED_CONSENTS,
  TERMS_NOT_FINAL,
  type ConsentItem,
} from "@/lib/consent";
import { formatDay } from "@/lib/format";

/**
 * 10 설정 — 동의 관리.
 *
 * 🚨 **필수와 선택을 같은 컨트롤로 그리지 않는다.** 필수 3건은 철회 버튼이 아예 없고 잠금
 *    아이콘 + 왜 못 끄는지가 글자로 선다. 선택 2건만 켜고 끈다 — 같은 스위치를 나란히 두면
 *    "다 끌 수 있다" 고 말하는 셈이고, 눌렀을 때와 다르다 (`lib/consent.ts` 주석).
 *
 * 🚨 **승인 게이트가 아니다.** 승인 게이트는 캘린더 쓰기·건강 기록 확정 딱 2곳이고 늘리지
 *    않는다 (최상위 CLAUDE.md §2) — `btn-approve` 도 `caution` 색도 쓰지 않는다.
 *
 * 🚨 **철회 확정에 `danger` 를 쓰지 않는다.** 디자인 시스템이 처음 "동의 철회" 를 파괴적
 *    확정으로 적은 것은 동의 4건이 전부 **필수**여서 철회가 탈퇴에 가까웠을 때다. 지금
 *    끌 수 있는 둘은 되돌릴 수 있고(같은 시트가 "다시 켤 수 있어요" 라고 말한다) 기록도
 *    지워지지 않는다. 빨강을 칠하면 화면이 **철회권 자체를 말리는** 것처럼 읽히는데,
 *    이 화면의 존재 이유가 그 권리를 돌려주는 것이다. 빨강은 보호자 연결 끊기에만 남긴다.
 *
 * 🚨 **끄는 쪽과 켜는 쪽에 다른 목록을 보여준다** (`blocks` / `enables`). `blocks` 를 켜는
 *    시트에 돌려 쓰면 "동의하면 동작해요" 아래에 "이 기능이 멈춰요" 가 붙는다.
 *
 * 🚨 **확인 시트는 예측을 쓰지 않는다.** 일어날 일만 적는다. 되살리는 길을 넓게 약속하지도
 *    않는다 — 다시 켜는 것은 되지만, 그 사이에 안 만들어진 제안은 안 돌아온다.
 *
 * 🚨 **상세 전문은 통째로 `font-doc`(Pretendard) 이다.** 불리한 조항을 놓치지 않고 읽어야
 *    하는 글이라 손글씨의 교환(가독성↓ 온기↑)을 하지 않는다. 한 화면에서 두 서체를 섞지
 *    않으려고 시트 전체에 건다 (디자인 시스템 §4).
 */

/** 화면 왼쪽 타일. 스코프마다 뜻이 좁은 사물 하나씩. */
const CONSENT_ICON = {
  service_terms: FileText,
  privacy_account: UserRound,
  child_basic: FileText,
  child_health: ShieldCheck,
  location: MapPin,
} as const;

type Pending = { item: ConsentItem; next: "granted" | "withdrawn" };

export function ConsentSection({ childId }: { childId: string }) {
  const queryClient = useQueryClient();
  const [detail, setDetail] = useState<ConsentItem | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  /**
   * 🚨 **바뀐 것을 소리로도 알린다.** 시트가 닫히고 줄의 글자가 바뀌는 것이 전부라, 화면을
   *    안 보는 사람에게는 아무 일도 안 일어난 것과 같다. 🚨 토스트를 쓰지 않는다 — 토스트는
   *    "조용히 되돌아간 실패" 자리이고(문서 §7), 성공은 화면이 이미 말하고 있다.
   */
  const [announcement, setAnnouncement] = useState("");

  const consents = useQuery({
    queryKey: qk.consents(childId),
    queryFn: () => api.get<ConsentsResponse>("/consents", { query: { child_id: childId } }),
  });

  const change = useMutation({
    mutationFn: ({ item, next }: Pending) => {
      const body: ConsentRequest = {
        scope: item.scope,
        action: next,
        policy_version: CONSENT_POLICY_VERSION,
        // 아이 스코프는 보호자임을 확인한 표시를 함께 보낸다 (계약서 §04).
        ...(item.target === "child" ? { child_id: childId, guardian_attested: true } : {}),
      };
      return api.post<ConsentResponse>("/consents", body);
    },
    // 🚨 낙관적 업데이트를 쓰지 않는다. 서버가 확정하기 전에 켜진 것처럼 그리면
    //    동의 상태를 화면이 먼저 바꾸는 셈이 된다 (apps/web/CLAUDE.md §3).
    onSuccess: (_data, variables) => {
      setPending(null);
      setAnnouncement(
        `${variables.item.shortLabel} 동의를 ${variables.next === "granted" ? "받았어요" : "철회했어요"}.`,
      );
      void queryClient.invalidateQueries({ queryKey: qk.consents(childId) });
    },
  });

  const effective = consents.data?.effective ?? {};
  const grantedAt = (scope: string) =>
    consents.data?.history.find((h) => h.scope === scope && h.action === "granted")?.acted_at;
  const withdrawnAt = (scope: string) =>
    consents.data?.history.find((h) => h.scope === scope && h.action === "withdrawn")?.acted_at;

  if (consents.isPending) {
    return <SkeletonBlock label="동의 현황을 불러오는 중" />;
  }

  if (consents.isError) {
    return (
      <CardFailed>
        <p>동의 현황을 불러오지 못했어요.</p>
        <p className="mt-1">잠시 뒤에 다시 열어 주세요.</p>
      </CardFailed>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {/* 값이 바뀐 뒤 한 문장. 자리를 차지하지 않고 소리로만 남는다. */}
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <div>
        {/* 🚨 두 무리 다 머리말을 단다. 한쪽에만 라벨이 있으면 나머지가 "그냥 목록" 으로
            읽혀서, 이 화면이 가르려던 것(내가 쥔 것 / 못 끄는 것)이 사라진다.
            🚨 **자물쇠 아이콘을 붙이지 않는다.** 열린 자물쇠와 닫힌 자물쇠는 16px 에서 거의
            같은 모양이라, 두 머리줄을 가르는 대신 헷갈리게 만든다 — 뜻은 글자가 진다 (§4). */}
        <h3 className="text-label text-ink-muted mb-2">켜고 끌 수 있는 동의</h3>
        <SettingsGroup>
          {OPTIONAL_CONSENTS.map((item) => {
            const on = effective[item.scope] === true;
            const at = on ? grantedAt(item.scope) : withdrawnAt(item.scope);
            const busy = change.isPending && pending?.item.scope === item.scope;

            return (
              <SettingsInfoRow
                key={item.scope}
                icon={CONSENT_ICON[item.scope]}
                title={item.shortLabel}
                /* 🚨 상태를 색이 아니라 글자로 낸다 — 옆에 버튼이 있어서 색만으로는
                 "지금 켜짐" 과 "누르면 켜짐" 이 구분되지 않는다. */
                status={on ? "동의함" : "동의하지 않음"}
                /* 🚨 이력이 없으면 아무 말도 하지 않는다. "다룬 적이 없어요" 는 가입할 때
                 받은 동의에 대해 **거짓**이고(가입 시점 행은 이 목록에 안 잡힐 수 있다),
                 상태는 바로 위 줄이 이미 말한다. */
                note={at ? `${formatDay(at)}에 ${on ? "동의했어요" : "철회했어요"}` : undefined}
                /* 🚨 **선택 동의에도 전문으로 가는 길을 둔다.** 이 둘(민감정보 제23조 ·
                   위치정보법 제19조)이야말로 별도 동의라 무엇에 동의하는지를 읽을 수
                   있어야 하는데, 켜고 끄는 버튼만 두면 **읽으려고 철회를 눌러야** 한다.
                   가입 화면은 이미 전문을 보여주므로, 없으면 설정이 가입보다 약해진다.
                   🚨 그 길을 오른쪽 버튼으로 **쌓지 않는다** — 줄이 내용의 두 배로 늘고
                   오른쪽 열이 줄마다 하나와 둘을 오간다 (`SettingsInfoRow` 주석). */
                onTitleClick={() => setDetail(item)}
                action={
                  <Button
                    variant="tertiary"
                    size="compact"
                    disabled={change.isPending}
                    onClick={() => setPending({ item, next: on ? "withdrawn" : "granted" })}
                  >
                    {busy ? <Spinner /> : null}
                    {on ? "철회하기" : "동의하기"}
                  </Button>
                }
              />
            );
          })}
        </SettingsGroup>
      </div>

      <div>
        <h3 className="text-label text-ink-muted mb-2">끌 수 없는 동의</h3>
        <SettingsGroup>
          {REQUIRED_CONSENTS.map((item) => (
            <SettingsInfoRow
              key={item.scope}
              icon={CONSENT_ICON[item.scope]}
              title={item.shortLabel}
              /* 🚨 **여기도 상태가 먼저다.** 이유만 적어 두면 제목이 "지금 이렇게 되어
                 있어요" 인 화면에서 이 셋만 지금 어떤지를 말하지 않는다. 약관 버전이
                 올라 필수 스코프가 꺼지면(콜백 화면이 이 화면으로 보내는 그 경우)
                 줄이 똑같이 그려져서, 요약의 분수만 조용히 줄어든다. */
              status={effective[item.scope] === true ? "동의함" : "동의하지 않음"}
              note={item.blocks[0]}
              /* 🚨 **오른쪽을 비운다.** 여기엔 할 수 있는 일이 없고, 그게 이 무리의 사실이다.
                 버튼을 하나 세워 두면 오른쪽 열이 "무엇이든 할 수 있는 자리" 로 읽힌다. */
              onTitleClick={() => setDetail(item)}
            />
          ))}
        </SettingsGroup>
        {/* 🚨 "탈퇴하면 됩니다" 라고 쓰지 않는다 — 탈퇴 엔드포인트가 없고 유예기간도
            미정이라(docs/api/auth-kakao-v1.md §미결), 지금 없는 길을 안내하게 된다. */}
        <p className="text-caption text-ink-subtle mt-2">
          {/* 🚨 개수를 글자로 박지 않는다. 필수/선택은 `lib/consent.ts` 가 정하고 여기서
              세는데, "이 셋은" 처럼 박아 두면 목록이 바뀔 때 화면만 옛말을 한다
              (실제로 아이 건강이 선택으로 갔다가 다시 필수로 돌아왔다). */}
          이 {REQUIRED_CONSENTS.length}건은 서비스가 성립하는 근거라 화면에서 끌 수 없어요. 그만
          쓰고 싶다면 계정을 정리하는 방법을 준비하고 있어요.
        </p>
      </div>

      {/* 내용 보기 — 되돌릴 수 있는 시트라 스크림 탭·ESC 로 닫힌다. */}
      <BottomSheet
        open={detail !== null}
        onClose={() => setDetail(null)}
        variant="document"
        title={detail?.label ?? ""}
        description={detail?.legalBasis}
        footer={
          /* 🚨 가입 동의 화면과 **같은 시트, 같은 버튼**이다. 한쪽만 채운 버튼이면
             같은 것을 여는 두 화면이 다른 무게로 읽힌다. */
          <Button block variant="secondary" onClick={() => setDetail(null)}>
            닫기
          </Button>
        }
      >
        {detail ? <ConsentDetail item={detail} /> : null}
      </BottomSheet>

      {/* 켜고 끄기 확정 */}
      <BottomSheet
        open={pending !== null}
        onClose={() => {
          if (!change.isPending) setPending(null);
        }}
        title={
          pending?.next === "withdrawn"
            ? `${pending.item.shortLabel} 동의를 철회할까요?`
            : `${pending?.item.shortLabel ?? ""} 동의를 받을까요?`
        }
        description={
          pending?.next === "withdrawn"
            ? "철회하면 아래가 멈춰요. 설정에서 다시 켤 수 있어요."
            : "동의하면 아래가 동작해요. 설정에서 언제든 다시 끌 수 있어요."
        }
        footer={
          pending ? (
            <div className="flex gap-2">
              <Button
                variant="tertiary"
                className="flex-1"
                disabled={change.isPending}
                onClick={() => setPending(null)}
              >
                그대로 둘게요
              </Button>
              <Button
                /* 🚨 철회에도 `danger` 를 쓰지 않는다 (위 주석). 되돌릴 수 있는 행동이고,
                   빨강을 칠하면 화면이 철회를 말리는 것처럼 읽힌다. */
                className="flex-1"
                disabled={change.isPending}
                onClick={() => change.mutate(pending)}
              >
                {change.isPending ? <Spinner /> : null}
                {pending.next === "withdrawn" ? "철회할게요" : "동의할게요"}
              </Button>
            </div>
          ) : null
        }
      >
        {pending ? (
          <div className="flex flex-col gap-4">
            <ul className="text-body text-ink marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
              {(pending.next === "withdrawn"
                ? pending.item.blocks
                : (pending.item.enables ?? pending.item.blocks)
              ).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            {change.isError ? (
              <CardFailed>
                <p>바꾸지 못했어요. 지금까지의 동의 상태는 그대로예요.</p>
              </CardFailed>
            ) : null}
          </div>
        ) : null}
      </BottomSheet>
    </div>
  );
}

/**
 * 🚨 약관 전문이 아니다. 보관 기간·삭제 범위가 미정이라(최상위 CLAUDE.md §10) 정식 문구를
 *    쓸 수 없고, 없는 조항을 지어 넣으면 그대로 배포된다. 확정된 사실만 보여주고 아직
 *    최종본이 아니라는 것을 화면에 밝힌다 (가입 동의 화면과 같은 규칙).
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
