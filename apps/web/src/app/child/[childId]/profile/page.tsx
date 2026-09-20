"use client";

import { useQuery } from "@tanstack/react-query";
import { Pencil, Plus, Ruler, Shield, type LucideIcon } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ChildIdentityCard, ChildIdentitySheet } from "@/components/child-identity";
import { ChildNav } from "@/components/child-nav";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { GrowthLatestCard } from "@/components/growth-log-list";
import { GrowthSheet } from "@/components/growth-sheet";
import { HealthSafetyList, HealthSafetySheet } from "@/components/health-safety-list";
import { HealthSafetyScanSheet } from "@/components/health-safety-scan-sheet";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { CountChip } from "@/components/ui/chip";
import { EmptyState } from "@/components/ui/empty-state";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { useChildId } from "@/hooks/use-child-id";
import { EARLIEST_BIRTH_DATE } from "@/lib/date-bounds";
import {
  api,
  isApiError,
  qk,
  type ChildProfile,
  type GrowthLog,
  type GrowthLogsResponse,
  type HealthSafety,
  type HealthSafetyListResponse,
} from "@/lib/api";

export default function ChildProfilePage() {
  return (
    <AuthGate>
      <ChildProfileScreen />
    </AuthGate>
  );
}

function ChildProfileScreen() {
  const childId = useChildId();

  const profile = useQuery({
    queryKey: qk.childProfile(childId),
    queryFn: () => api.get<ChildProfile>(`/children/${childId}`),
  });

  const growth = useQuery({
    queryKey: qk.growth(childId),
    queryFn: () => api.get<GrowthLogsResponse>(`/children/${childId}/growth`),
  });

  const safety = useQuery({
    queryKey: qk.healthSafety(childId),
    queryFn: () => api.get<HealthSafetyListResponse>(`/children/${childId}/health-safety`),
  });

  return (
    <Screen className="gap-6" nav={<ChildNav active="profile" />}>
      {/* 🚨 **머리에 아이 이름·나이를 다시 세우지 않는다.** 바로 아래 "부르는 이름" 구역의
          별명 입력과 생일 입력이 같은 값을 들고 있어서, 머리에 또 쓰면 한 화면에서 같은 사실이
          두 번 선다. 제목은 화면을 가리키고, 아이가 누구인지는 그 구역이 진다. */}
      <header>
        <PageTitle>아이 프로필</PageTitle>
        <p className="text-body-sm text-ink-muted mt-2">
          부르는 이름과 키·몸무게, 알레르기를 여기서 관리해요.
        </p>
      </header>

      <IdentitySection childId={childId} query={profile} />

      <GrowthSection childId={childId} query={growth} />

      <SafetySection childId={childId} query={safety} />
    </Screen>
  );
}

/* ── 구역 틀 ──────────────────────────────────────────────────────────── */

/**
 * 구역 제목 + 설명 + 내용.
 *
 * 🚨 **라벨은 `label` / `brand` 다** (디자인 시스템 §2-2 "브랜드색이 서는 자리" 표 첫 줄 ·
 *    03 홈의 `Section` 과 같은 device). 이 화면은 한동안 `section` / `ink` 를 썼는데,
 *    그게 §2-2 가 경고한 상태를 그대로 만들었다 — **"브랜드를 어디에 둘지 정해 두지 않으면
 *    화면이 회색 카드의 나열이 된다."** 이 화면은 도메인 4색을 하나도 안 쓰기로 했고(위 머리말)
 *    `caution`·`danger` 는 알레르기 구역 전용이라, 브랜드가 규칙적으로 들어오는 자리가
 *    섹션 라벨 말고는 남지 않는다.
 *
 * 🚨 **구역을 카드로 감싸지 않는다** — 세 구역을 전부 흰 상자에 넣으면 상자가 줄줄이 서고
 *    (04 저장 결과에서 한 번 그랬다), 무엇이 다른 무게인지가 사라진다. 무게는 **구역 안의
 *    내용**이 진다. 카드는 구역이 아니라 **묶이는 내용**이 받는다 (03 홈과 같은 처리).
 *
 * `count` 는 **쌓인 건수를 그대로 보여주는 자리**다 (최상위 CLAUDE.md §2 · `EmptyState` 가
 * 건수를 필수로 받는 것과 같은 이유). 🚨 **여기에 키·몸무게 값을 넣지 않는다** — 건수는
 * 기록의 수고 지표가 아니지만, 잰 값이 큰 글자로 서는 순간 그건 지표다 (`DESIGN.md` Don't).
 *
 * 🚨 **0 건이면 넘기지 않는다.** 그때는 `EmptyState` 가 같은 숫자를 훨씬 크게 말하고 있어서,
 *    한 구역 안에 같은 건수가 두 번 선다 (실제로 그렇게 보였다). 건수를 숨기는 게 아니라
 *    **말하는 자리를 하나로 두는 것**이다 — 0 도 정보라는 규칙은 `EmptyState` 가 계속 진다.
 */
function Section({
  title,
  description,
  count,
  action,
  children,
}: {
  title: string;
  description: string;
  /** 쌓인 건수. 아직 안 불러왔거나 0 이면 넘기지 않는다 (위 주석). */
  count?: number;
  /**
   * 이 구역에 **더하는** 행동. 🚨 **목록 아래 전체 폭 버튼으로 두지 않는다** — `secondary` 는
   * `surface` 바탕에 테두리라 목록 상자와 실루엣이 같고, 목록 바로 밑에 붙으면 둥근 사각형이
   * 두 개 연달아 선다. 구역이 셋이라 그것만으로 화면이 **상자의 나열**이 됐다.
   * 머리줄로 올리면 상자 둘이 사라지고, "무엇이 몇 건 · 여기서 더한다" 가 한 줄에 모인다.
   */
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        {/* 🚨 라벨 · 건수 · 행동이 한 줄이다. 건수를 라벨 아래로 내리지 않는다 —
            설명 줄과 같은 높이가 되면 "몇 건인지" 가 설명의 일부로 읽힌다.
            🚨 `items-center` 다. 버튼이 있는 줄에서 `items-baseline` 을 쓰면 버튼 상자가
            글자 기준선에 매달려 라벨보다 아래로 처진다.
            🚨 **줄 높이를 `min-h-touch` 로 깔지 않는다.** 버튼이 이미 자기 높이(44)를 갖고
            있어서 있는 줄은 어차피 44 인데, 없는 줄("부르는 이름")까지 44 가 되면 13px 라벨
            위아래로 빈 공간이 15px 씩 생겨 라벨과 설명이 남남처럼 떨어진다. */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-baseline gap-2">
            <h2 className="text-label text-brand">{title}</h2>
            {count !== undefined ? <CountChip>{count}건</CountChip> : null}
          </div>
          {action}
        </div>
        <p className="text-body-sm text-ink-muted mt-1">{description}</p>
      </div>
      {children}
    </section>
  );
}

/**
 * 구역에 **더하는** 행동. 아이콘 하나다.
 *
 * 🚨 **글자 버튼이 아니다.** 13px 라벨 옆에 테두리 있는 글자 버튼이 서면 그 줄에서 제일 무거운
 *    것이 버튼이 되고, 구역이 셋이라 같은 상자가 화면에 여럿 생긴다 (목록 아래 전체 폭 버튼을
 *    걷어낸 것과 같은 이유 · 디자인 시스템 §7 버튼).
 * 🚨 **`label` 을 빼지 않는다.** 글자가 없으니 스크린리더에 남는 것이 그것뿐이고, 툴팁도
 *    거기서 나온다 (`IconButton`). 무엇에 더하는지까지 담는다 — 화면에 같은 아이콘이 둘이라
 *    "추가" 만으로는 어느 구역인지 알 수 없다.
 * 🚨 아이콘이 단독 신호가 되지 않는다 — 바로 왼쪽에 구역 이름이 글자로 서 있다 (§3 · §10).
 */
function SectionAction({
  label,
  icon: Icon = Plus,
  onClick,
}: {
  label: string;
  /** 기본은 더하기다. 고치는 구역만 연필을 넘긴다 — 뜻이 다르면 모양도 달라야 한다. */
  icon?: LucideIcon;
  onClick: () => void;
}) {
  return (
    <IconButton label={label} onClick={onClick} className="-my-1">
      <Icon aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
    </IconButton>
  );
}

/** 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3). `CardFailed` 는 뉴트럴이다. */
function SectionError({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <CardFailed>
      <p className="text-body text-ink">{what}</p>
      <Button variant="secondary" size="compact" onClick={onRetry} className="mt-3">
        다시 불러오기
      </Button>
    </CardFailed>
  );
}

/* ── ㉠ 부르는 이름 ───────────────────────────────────────────────────── */

function IdentitySection({
  childId,
  query,
}: {
  childId: string;
  query: ReturnType<typeof useQuery<ChildProfile>>;
}) {
  const [editing, setEditing] = useState(false);

  return (
    <Section
      title="부르는 이름"
      description="아이를 부르는 말과 생일이에요."
      // 🚨 고치는 것은 드문 일이라 **행동으로** 둔다 — 입력칸을 늘 펼쳐 두지 않는다
      //    (`ChildIdentityCard` 머리말). 다른 두 구역과 같은 머리줄 문법이다.
      action={
        query.data ? (
          <SectionAction
            label="부르는 이름 고치기"
            icon={Pencil}
            onClick={() => setEditing(true)}
          />
        ) : undefined
      }
    >
      {query.isPending ? (
        <SkeletonBlock label="아이 정보를 불러오는 중" />
      ) : query.isError ? (
        <SectionError what="아이 정보를 불러오지 못했어요" onRetry={() => void query.refetch()} />
      ) : (
        <>
          <ChildIdentityCard profile={query.data} />
          {/* 🚨 열 때마다 새로 만든다 (`key`) — 폼이 `useState` 로 값을 들고 있어서 같은
              인스턴스를 재사용하면 지난번에 고치다 만 값이 남는다. */}
          {editing ? (
            <ChildIdentitySheet
              key={query.data.id}
              open
              onClose={() => setEditing(false)}
              childId={childId}
              profile={query.data}
              earliestBirthDate={EARLIEST_BIRTH_DATE}
            />
          ) : null}
        </>
      )}
    </Section>
  );
}

/* ── ㉡ 키 · 몸무게 ──────────────────────────────────────────────────── */

/**
 * 🚨 **여기는 가장 최근 한 줄만 세운다.** 쌓인 목록과 변화 그래프는 11-1 상세
 * (`profile/growth`)가 진다 — 이유는 `GrowthLatestCard` 머리말에 있다.
 *
 * 🚨 **화면의 말은 "키 · 몸무게" 고 코드의 이름은 `growth` 다.** 07 이 관찰/기록 ·
 *    프로필/기억으로 갈라 두는 것과 같은 처리다 (apps/web/CLAUDE.md §3) — 화면 문구만
 *    바꾸고 엔드포인트·쿼리 키·타입 이름을 따라 바꾸지 않는다. 그쪽은 계약서를 따른다.
 * 🚨 상세로 가는 길이 **줄 자체**다. 머리줄의 `Plus` 말고 다른 버튼을 늘리지 않는다.
 */
function GrowthSection({
  childId,
  query,
}: {
  childId: string;
  query: ReturnType<typeof useQuery<GrowthLogsResponse>>;
}) {
  const [adding, setAdding] = useState(false);

  const logs = query.data?.items ?? [];
  /**
   * 🚨 **서버가 준 순서를 믿지 않는다.** 목은 최신이 위지만 계약서가 정렬을 약속한 적이 없고,
   *    여기서 틀리면 화면이 **오래된 값을 "지금 얼마" 로** 말한다. 날짜 비교는 계산이 아니라
   *    같은 형식(`YYYY-MM-DD`)의 문자열 정렬이다 (apps/web/CLAUDE.md §4 는 **나이·기간**을 막는다).
   */
  const latest = logs.reduce<GrowthLog | null>(
    (best, log) => (best === null || log.measured_on > best.measured_on ? log : best),
    null,
  );

  return (
    <Section
      title="키 · 몸무게"
      description="가장 최근에 잰 것만 보여줘요. 눌러서 지난 기록과 변화를 볼 수 있어요."
      // 🚨 불러오기 전과 0 건일 때는 넘기지 않는다 — 앞은 "아직 모름" 이라 0 을 그리면
      //    거짓말이고, 뒤는 바로 아래 `EmptyState` 가 같은 숫자를 이미 말한다.
      count={logs.length > 0 ? logs.length : undefined}
      action={
        // 🚨 승인 게이트가 아니다 — 잘못 적으면 그 줄을 지우면 된다 (최상위 §2).
        <SectionAction label="키·몸무게 새로 적기" onClick={() => setAdding(true)} />
      }
    >
      {query.isPending ? (
        <SkeletonBlock label="측정 기록을 불러오는 중" />
      ) : query.isError ? (
        <SectionError what="측정 기록을 불러오지 못했어요" onRetry={() => void query.refetch()} />
      ) : latest === null ? (
        // 🚨 빈 상태를 사과문으로 쓰지 않는다. 쌓인 건수를 그대로 보여준다 (`EmptyState`).
        // 🚨 상세로 가는 길을 여기 붙이지 않는다 — 가 봐야 같은 0 건이다.
        <EmptyState
          icon={Ruler}
          title="아직 잰 기록이 없어요"
          description="어린이집 신체검사나 병원에서 잰 날, 여기에 적어 두면 돼요."
          count={0}
          countLabel="적어 둔 기록"
        />
      ) : (
        <GrowthLatestCard log={latest} href={`/child/${childId}/profile/growth`} />
      )}

      <GrowthSheet open={adding} onClose={() => setAdding(false)} childId={childId} />
    </Section>
  );
}

/**
 * 더하는 길 고르기. 🚨 **승인 게이트가 아니다** — 아직 아무것도 저장하지 않으므로
 * `caution` 도 `btn-approve` 도 쓰지 않고, 스크림 탭으로 닫힌다.
 *
 * 🚨 **직접 적기가 위다.** 사진이 더 편해 보여도, 검사지가 없는 보호자가 대부분이고
 *    이 제품의 기본 경로는 보호자가 직접 확인한 것을 적는 것이다 (최상위 §2).
 */
function AddSafetySheet({
  open,
  onClose,
  onManual,
  onPhoto,
}: {
  open: boolean;
  onClose: () => void;
  onManual: () => void;
  onPhoto: () => void;
}) {
  return (
    <BottomSheet open={open} onClose={onClose} title="알레르기 · 건강 기록 추가">
      <div className="flex flex-col gap-2">
        <AddSafetyOption
          title="직접 적기"
          description="확인한 것을 한 건씩 적어요."
          onClick={onManual}
        />
        <AddSafetyOption
          title="검사지 사진에서 가져오기"
          description="알레르기 검사지를 찍으면 적힌 것을 옮겨 와요. 확인하고 한 번에 등록해요."
          onClick={onPhoto}
        />
      </div>
    </BottomSheet>
  );
}

/**
 * 고르는 줄. 🚨 **`Button` 이 아니다** — 설명이 두 줄까지 늘어나고 왼쪽 정렬이라 버튼 사양
 * (가운데 정렬 · 한 줄)과 맞지 않는다. 시트 안의 목록 줄이라 `line` 1px 로 갈린다.
 */
function AddSafetyOption({
  title,
  description,
  onClick,
}: {
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-card border-line bg-surface ease-standard active:bg-surface-muted hover:bg-surface-muted min-h-touch flex flex-col items-start gap-1 border px-4 py-3 text-left transition-colors duration-120"
    >
      <span className="text-body text-ink">{title}</span>
      <span className="text-body-sm text-ink-muted">{description}</span>
    </button>
  );
}

/* ── ㉢ 알레르기 · 건강 (🚨 승인 게이트 ㉡) ──────────────────────────── */

function SafetySection({
  childId,
  query,
}: {
  childId: string;
  query: ReturnType<typeof useQuery<HealthSafetyListResponse>>;
}) {
  /**
   * 더하는 길이 둘이다 — 직접 적기와 검사지 사진. 🚨 **시트를 겹쳐 열지 않는다**
   * (`<dialog>` 두 장이 포개지면 포커스 트랩이 둘이 된다). 한 번에 하나만 열리게
   * 상태 하나로 가른다.
   */
  const [mode, setMode] = useState<null | "choose" | "manual" | "scan">(null);
  const [photo, setPhoto] = useState<File | null>(null);
  /** 고치는 중인 기록. 있으면 같은 승인 게이트 시트가 고치기로 열린다. */
  const [editing, setEditing] = useState<HealthSafety | null>(null);

  /**
   * 🚨 **파일 입력은 사용자 제스처에서 열어야 한다.** 시트를 닫고 나서 열려고 하면 제스처가
   *    이미 소비돼 브라우저가 막는다 — 고르는 줄에서 바로 `click()` 한다.
   *    `accept="image/*"` 하나면 OS 가 촬영·앨범을 알아서 같이 준다.
   */
  const photoInputRef = useRef<HTMLInputElement>(null);

  const items = query.data?.items ?? [];

  return (
    <Section
      title="알레르기 · 건강"
      description="보호자가 직접 확인한 것만 저장돼요. AI 는 이 목록을 만들지도 고치지도 못해요."
      count={items.length > 0 ? items.length : undefined}
      action={<SectionAction label="알레르기·건강 기록 추가" onClick={() => setMode("choose")} />}
    >
      {query.isPending ? (
        <SkeletonBlock label="안전 정보를 불러오는 중" />
      ) : isApiError(query.error, "consent_required") ? (
        // 🚨 일반 실패로 뭉뚱그리지 않는다 — 다시 시도해도 같은 403 이다.
        <ConsentRequiredCard
          childId={childId}
          error={query.error}
          what="안전 정보를 불러올 수 없고"
        />
      ) : query.isError ? (
        <SectionError what="안전 정보를 불러오지 못했어요" onRetry={() => void query.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Shield}
          title="등록된 것이 아직 없어요"
          description="알레르기나 지병이 확인되면 여기에 적어 두세요. 식사 제안이 이 목록을 보고 걸러요."
          count={0}
          countLabel="등록된 항목"
        />
      ) : (
        <HealthSafetyList childId={childId} items={items} onEdit={setEditing} />
      )}

      {/* 🚨 화면에 보이지 않지만 자리는 여기다 — 고르는 시트가 닫힌 뒤에도 같은 입력을 쓴다. */}
      <input
        ref={photoInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0] ?? null;
          // 같은 파일을 다시 고를 수 있게 값을 비운다 (안 비우면 onChange 가 안 온다).
          e.target.value = "";
          if (!file) return;
          setPhoto(file);
          setMode("scan");
        }}
      />

      <AddSafetySheet
        open={mode === "choose"}
        onClose={() => setMode(null)}
        onManual={() => setMode("manual")}
        onPhoto={() => photoInputRef.current?.click()}
      />

      <HealthSafetySheet open={mode === "manual"} onClose={() => setMode(null)} childId={childId} />

      {/* 🚨 고치는 기록이 바뀌면 시트를 새로 만든다 (`key`) — 폼이 `useState` 로 값을 들고 있어서
          같은 인스턴스를 재사용하면 앞 기록의 입력이 남는다. */}
      {editing ? (
        <HealthSafetySheet
          key={editing.id}
          open
          onClose={() => setEditing(null)}
          childId={childId}
          item={editing}
        />
      ) : null}

      {/* 🚨 사진이 바뀌면 시트를 새로 만든다 (`key`). 미리보기 URL 과 "한 번만 읽는다" 가드를
          `useState` 초기값·`useRef` 로 들고 있어서, 같은 인스턴스를 재사용하면 두 번째 사진이
          첫 번째 사진의 결과를 그대로 보여준다. */}
      {photo ? (
        <HealthSafetyScanSheet
          key={`${photo.name}:${photo.lastModified}`}
          open={mode === "scan"}
          onClose={() => {
            setMode(null);
            setPhoto(null);
          }}
          childId={childId}
          file={photo}
          registeredLabels={items.map((item) => item.label)}
        />
      ) : null}
    </Section>
  );
}
