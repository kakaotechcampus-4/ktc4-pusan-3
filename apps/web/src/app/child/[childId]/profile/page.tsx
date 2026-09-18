"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ruler, Shield } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { GrowthLogList } from "@/components/growth-log-list";
import { HealthSafetyList, HealthSafetySheet } from "@/components/health-safety-list";
import { HealthSafetyScanSheet } from "@/components/health-safety-scan-sheet";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { CountChip } from "@/components/ui/chip";
import { DateField } from "@/components/ui/date-field";
import { EmptyState } from "@/components/ui/empty-state";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Select } from "@/components/ui/select";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { useChildId } from "@/hooks/use-child-id";
import {
  api,
  isApiError,
  qk,
  type ChildProfile,
  type Gender,
  type GrowthLog,
  type GrowthLogsResponse,
  type HealthSafetyListResponse,
  type UpdateChildRequest,
} from "@/lib/api";

/**
 * 11 아이 프로필 — 프로토타입에 없는 화면이다 (#74).
 *
 * **왜 한 화면인가** — 아이에 대한 진실은 세 가지고, 셋 다 **무게가 다르다.**
 *   ㉠ 거의 안 바뀌는 것 (부르는 이름: 별명 · 생일 · 성별)
 *   ㉡ 잴 때마다 새로 쌓이는 것 (재 둔 것: 키 · 몸무게)
 *   ㉢ 보호자만 확정할 수 있는 것 (알레르기 · 건강 — 🚨 승인 게이트 ㉡)
 * 07 이 기록(줄)과 기억(카드)을 모양으로 갈라 놓은 것과 같은 수법으로, 여기서도 셋을
 * **모양과 무게로** 가른다. 탭으로 나누지 않은 것은 셋이 짧고 서로를 참조하기 때문이다.
 *
 * 🚨 **성장 차트 · 백분위 · 또래 비교 · 증감을 만들지 않는다.** `DESIGN.md` 의
 *    "부모가 자기 아이를 지표로 보게 하지 않는다" 이고, 증감은 이 제품이 하지 않기로 한
 *    **발달 평가**다 (최상위 CLAUDE.md §2 · 스펙 아웃).
 *
 * 🚨 **아이 사진 · 아바타를 두지 않는다.** 수집이 늘고(§2 개인정보), 워드마크도 없는
 *    제품이라 브랜드가 서는 자리도 아니다.
 *
 * 🚨 **승인 게이트를 늘리지 않는다** (최상위 §2). 별명 · 생일 · 성별 · 측정 기록은 되돌릴 수
 *    있어서 게이트가 아니다 — `btn-approve` 와 `caution` 은 알레르기 등록에만 선다.
 *
 * 🚨 **나이를 계산하지 않는다.** `age_display` 는 서버 문구다 (CLAUDE.md §3).
 *
 * ⚠️ `GET/PATCH /children/{cid}` 의 `gender` 와 `/growth` 는 **계약서 v1 에 없다** (#75).
 *    지금은 MSW 목만 답한다 — 실서버 연결은 그 협의 이후다.
 */

const GENDER_OPTIONS = [
  { value: "unspecified", label: "밝히지 않을래요" },
  { value: "male", label: "남자아이" },
  { value: "female", label: "여자아이" },
] as const satisfies ReadonlyArray<{ value: Gender; label: string }>;

/**
 * 달력의 하한. 아이 서비스라 20년 전이면 충분하다 (01 화면과 같은 값).
 * 🚨 나이 계산이 아니다 — 고를 수 있는 범위를 정하는 것뿐이다 (apps/web/CLAUDE.md §4 예외).
 */
const EARLIEST_BIRTH_DATE = new Date(new Date().getFullYear() - 20, 0, 1);

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
          부르는 이름과 재 둔 것, 알레르기를 여기서 관리해요.
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
  return (
    <Section
      title="부르는 이름"
      description="아이를 부르는 말과 생일이에요."
    >
      {query.isPending ? (
        <SkeletonBlock label="아이 정보를 불러오는 중" />
      ) : query.isError ? (
        <SectionError what="아이 정보를 불러오지 못했어요" onRetry={() => void query.refetch()} />
      ) : (
        <IdentityForm childId={childId} profile={query.data} />
      )}
    </Section>
  );
}

function IdentityForm({ childId, profile }: { childId: string; profile: ChildProfile }) {
  const queryClient = useQueryClient();

  const [nickname, setNickname] = useState(profile.nickname);
  const [birthDate, setBirthDate] = useState(profile.birth_date);
  const [gender, setGender] = useState<Gender>(profile.gender);
  const [nicknameError, setNicknameError] = useState<string | null>(null);

  /**
   * 🚨 **서버 값이 바뀌면 손대지 않은 칸만 그 값으로 맞춘다.** `useState` 초기값은 첫 렌더에서
   *    한 번만 읽히므로, 배우자가 같은 아이의 별명을 고쳐서 이 쿼리가 새로 받아와도 폼은 옛
   *    값을 들고 있었다. 그러면 아래 `changes` 가 **남이 방금 저장한 값을 내 수정분으로 잡고**,
   *    저장 버튼이 켜진 채 그 수정을 되돌리자고 제안한다 — 공유 계정이라 실제로 나는 경로다.
   *
   * 🚨 **칸 단위로 맞춘다.** 세 칸을 통째로 덮으면 배우자가 생일을 고친 순간 내가 적고 있던
   *    별명이 말없이 사라진다. 지금 값이 마지막으로 받은 서버 값과 같을 때(= 안 건드린 칸)만
   *    새 값으로 바꾼다.
   *    ⚠️ 남는 경우가 하나 있다 — **같은 칸을 둘이 동시에** 고치면 내가 적던 쪽이 남고, 저장하면
   *    배우자 값을 덮는다. 그건 충돌이라 화면이 혼자 못 정한다 (지금은 내 입력을 지키는 쪽).
   *
   * 🚨 effect 가 아니라 렌더 중 조정이다 (React "Adjusting state when props change").
   *    effect 로 하면 옛 값으로 한 프레임을 먼저 그린다. TanStack Query 는 structural
   *    sharing 이라 내용이 같으면 참조가 그대로여서, 이 비교는 값이 실제로 바뀔 때만 걸린다.
   */
  const [synced, setSynced] = useState(() => ({
    nickname: profile.nickname,
    birth_date: profile.birth_date,
    gender: profile.gender,
  }));
  if (
    synced.nickname !== profile.nickname ||
    synced.birth_date !== profile.birth_date ||
    synced.gender !== profile.gender
  ) {
    if (nickname === synced.nickname) setNickname(profile.nickname);
    if (birthDate === synced.birth_date) setBirthDate(profile.birth_date);
    if (gender === synced.gender) setGender(profile.gender);
    setSynced({
      nickname: profile.nickname,
      birth_date: profile.birth_date,
      gender: profile.gender,
    });
  }

  /**
   * 🚨 **고친 것만 보낸다.** 안 고친 필드를 함께 올리면 두 보호자가 같은 화면을 열어 뒀을 때
   *    나중 저장이 남의 수정을 덮는다 (배우자·조부모가 같은 아이를 본다 · PRODUCT.md).
   */
  const changes: UpdateChildRequest = {
    ...(nickname.trim() !== profile.nickname ? { nickname: nickname.trim() } : {}),
    ...(birthDate !== profile.birth_date ? { birth_date: birthDate } : {}),
    ...(gender !== profile.gender ? { gender } : {}),
  };
  const dirty = Object.keys(changes).length > 0;

  const save = useMutation({
    mutationFn: (body: UpdateChildRequest) =>
      api.patch<{ child: ChildProfile }>(`/children/${childId}`, body),
    onSuccess: async () => {
      // 별명 하나가 홈 인사말과 `GET /me` 의 아이 목록까지 바꾼다. 아이 스코프를 통째로 무효화한다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
      await queryClient.invalidateQueries({ queryKey: qk.me() });
    },
  });

  function submit() {
    if (!nickname.trim()) {
      setNicknameError("별명을 적어주세요");
      return;
    }
    setNicknameError(null);
    save.mutate(changes);
  }

  return (
    <div className="flex flex-col gap-4">
      {/* 🚨 **입력을 카드로 감싸지 않는다.** 입력칸은 그 자체가 테두리 있는 면이라, 한 번
          감싸면 **상자 안의 상자**가 된다 — 한동안 그렇게 뒀고 화면이 상자의 나열로 읽혔다.
          목록 둘만 상자를 갖고 폼은 `canvas` 위에 서는 편이 세 구역의 리듬도 만든다
          (긴 입력 묶음 → 담긴 목록 → 담긴 목록).
          🚨 **hint 를 달지 않는다.** 구역 설명이 이미 "아이를 부르는 말과 생일이에요" 라고
          해서 같은 말이 두 층으로 쌓였고, 그만큼 "재 둔 것" 이 첫 화면 밖으로 밀렸다. */}
      <TextInput
        label="별명"
        value={nickname}
        onChange={(e) => setNickname(e.target.value)}
        error={nicknameError}
        autoComplete="off"
      />

      <DateField
        label="생일"
        value={birthDate}
        onChange={setBirthDate}
        fromDate={EARLIEST_BIRTH_DATE}
        toDate={new Date()}
      />

      {/* 🚨 기본값이 "밝히지 않을래요" 다. 수집을 늘리지 않는다는 원칙이 남아 있는 자리고,
          성별은 화면 표시 전용이라 비워 둬도 화면이 하는 일이 줄지 않는다 (#75). */}
      <Select label="성별" value={gender} options={GENDER_OPTIONS} onChange={setGender} />

      {/* 🚨 이 화면의 primary 는 이 버튼 하나다 (디자인 시스템 §7 — 한 화면에 primary 하나).
          🚨 비활성을 브랜드색 흐리게로 만들지 않는다 — `Button` 의 DISABLED 가 소유한다. */}
      <Button block onClick={submit} disabled={!dirty || save.isPending}>
        {save.isPending ? <Spinner /> : null}
        {save.isPending ? "저장하는 중이에요" : "고친 것 저장하기"}
      </Button>

      {/* 🚨 `role="status"` 로 알린다 — 버튼이 비활성으로 돌아가는 것 말고는 눈으로만 알 수
          있어서, 스크린리더 사용자는 저장됐는지 실패했는지 모른다 (`CorrectionButtons` 와 같음). */}
      {save.isError ? (
        <p role="status" className="text-body-sm text-ink-muted">
          저장하지 못했어요. 고친 것은 그대로 있으니 다시 눌러 주세요.
        </p>
      ) : save.isSuccess && !dirty ? (
        // 🚨 토스트를 쓰지 않는다 — 성공은 화면이 이미 말한다 (apps/web/CLAUDE.md §3).
        <p role="status" className="text-body-sm text-ink-muted">
          저장했어요.
        </p>
      ) : null}
    </div>
  );
}

/* ── ㉡ 재 둔 것 ──────────────────────────────────────────────────────── */

function GrowthSection({
  childId,
  query,
}: {
  childId: string;
  query: ReturnType<typeof useQuery<GrowthLogsResponse>>;
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const remove = useMutation({
    mutationFn: (log: GrowthLog) => {
      setDeletingId(log.id);
      return api.delete<void>(`/children/${childId}/growth/${log.id}`);
    },
    onSettled: async () => {
      setDeletingId(null);
      await queryClient.invalidateQueries({ queryKey: qk.growth(childId) });
    },
  });

  const logs = query.data?.items ?? [];

  return (
    <Section
      title="재 둔 것"
      description="키와 몸무게를 잰 날 그대로 쌓아 둬요. 또래와 견주지 않아요."
      // 🚨 불러오기 전과 0 건일 때는 넘기지 않는다 — 앞은 "아직 모름" 이라 0 을 그리면
      //    거짓말이고, 뒤는 바로 아래 `EmptyState` 가 같은 숫자를 이미 말한다.
      count={logs.length > 0 ? logs.length : undefined}
      action={
        // 🚨 승인 게이트가 아니다 — 잘못 적으면 그 줄을 지우면 된다 (최상위 §2).
        //    그래서 `tertiary` 이고, 이 화면의 primary 는 위 저장 버튼 하나다.
        <Button variant="tertiary" size="compact" onClick={() => setAdding(true)}>
          새로 적기
        </Button>
      }
    >
      {query.isPending ? (
        <SkeletonBlock label="측정 기록을 불러오는 중" />
      ) : query.isError ? (
        <SectionError what="측정 기록을 불러오지 못했어요" onRetry={() => void query.refetch()} />
      ) : logs.length === 0 ? (
        // 🚨 빈 상태를 사과문으로 쓰지 않는다. 쌓인 건수를 그대로 보여준다 (`EmptyState`).
        <EmptyState
          icon={Ruler}
          title="아직 잰 기록이 없어요"
          description="어린이집 신체검사나 병원에서 잰 날, 여기에 적어 두면 돼요."
          count={0}
          countLabel="적어 둔 기록"
        />
      ) : (
        <GrowthLogList logs={logs} onDelete={(log) => remove.mutate(log)} deletingId={deletingId} />
      )}

      {remove.isError ? (
        <p role="status" className="text-body-sm text-ink-muted">
          지우지 못했어요. 그 줄은 아직 목록에 있으니 다시 눌러 주세요.
        </p>
      ) : null}

      <GrowthSheet open={adding} onClose={() => setAdding(false)} childId={childId} />
    </Section>
  );
}

/**
 * 새 측정 기록. 🚨 **승인 게이트가 아니다** — `caution` 도 `btn-approve` 도 쓰지 않고,
 * 스크림 탭으로 닫힌다 (`dismissible` 기본값).
 */
function GrowthSheet({
  open,
  onClose,
  childId,
}: {
  open: boolean;
  onClose: () => void;
  childId: string;
}) {
  const queryClient = useQueryClient();

  const [measuredOn, setMeasuredOn] = useState(toToday);
  const [height, setHeight] = useState("");
  const [weight, setWeight] = useState("");
  /**
   * 🚨 **사유를 입력에 붙인다.** 시트 아래 떠 있는 문단으로 두면 보조기술이 그 문구를 어느
   *    칸의 문제인지 잇지 못한다 — `TextInput` 의 `error` 는 `aria-invalid` 와
   *    `aria-describedby` 를 함께 걸어 준다 (디자인 시스템 §7 입력).
   *    "둘 중 하나는 적어주세요" 처럼 칸 하나에 안 붙는 사유는 **첫 칸**이 받는다.
   */
  const [errors, setErrors] = useState<{ height?: string; weight?: string }>({});

  const save = useMutation({
    mutationFn: (body: { measured_on: string; height_cm?: number; weight_kg?: number }) =>
      api.post<{ log: GrowthLog }>(`/children/${childId}/growth`, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.growth(childId) });
      reset();
      onClose();
    },
  });

  function reset() {
    setMeasuredOn(toToday());
    setHeight("");
    setWeight("");
    setErrors({});
    save.reset();
  }

  function submit() {
    const h = parseMeasurement(height);
    const w = parseMeasurement(weight);

    if (h === "invalid" || w === "invalid") {
      setErrors({
        ...(h === "invalid" ? { height: "숫자로 적어주세요" } : {}),
        ...(w === "invalid" ? { weight: "숫자로 적어주세요" } : {}),
      });
      return;
    }
    // 🚨 한쪽만 재고 오는 날이 있다. 둘 다 비어 있을 때만 막는다.
    if (h === null && w === null) {
      setErrors({ height: "키나 몸무게 중 하나는 적어주세요" });
      return;
    }
    setErrors({});

    save.mutate({
      measured_on: measuredOn,
      ...(h !== null ? { height_cm: h } : {}),
      ...(w !== null ? { weight_kg: w } : {}),
    });
  }

  return (
    <BottomSheet
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="새로 재서 적기"
      description="둘 중 하나만 적어도 괜찮아요."
      footer={
        <Button block onClick={submit} disabled={save.isPending}>
          {save.isPending ? <Spinner /> : null}
          {save.isPending ? "적는 중이에요" : "적어 두기"}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <DateField
          label="잰 날"
          value={measuredOn}
          onChange={setMeasuredOn}
          fromDate={EARLIEST_BIRTH_DATE}
          toDate={new Date()}
        />

        {/* ⚠️ 입력을 16px 미만으로 내리지 않는다 — iOS 웹뷰가 화면을 확대하고 그대로 남는다
            (`TextInput` 주석). `inputMode="decimal"` 로 숫자 키패드만 띄운다. */}
        <TextInput
          label="키 · cm"
          value={height}
          onChange={(e) => setHeight(e.target.value)}
          error={errors.height}
          inputMode="decimal"
          autoComplete="off"
          placeholder="104.2"
        />

        <TextInput
          label="몸무게 · kg"
          value={weight}
          onChange={(e) => setWeight(e.target.value)}
          error={errors.weight}
          inputMode="decimal"
          autoComplete="off"
          placeholder="17.1"
        />

        {save.isError ? (
          <p role="status" className="text-body-sm text-ink-muted">
            적지 못했어요. 저장된 것은 없으니 다시 눌러 주세요.
          </p>
        ) : null}
      </div>
    </BottomSheet>
  );
}

/**
 * 빈 칸은 `null`, 숫자가 아니면 `"invalid"`.
 * 🚨 잘못 적은 값을 조용히 0 이나 `NaN` 으로 넘기지 않는다 — 아이 몸무게가 0kg 으로 쌓인다.
 */
function parseMeasurement(value: string): number | null | "invalid" {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0) return "invalid";
  return parsed;
}

/**
 * 🚨 오늘 날짜를 `toISOString()` 으로 만들지 않는다 — UTC 로 접혀서 한국 시간 자정~오전 9시
 *    사이에는 **하루가 밀린다** (`lib/format.ts` 의 같은 주의). 여기는 계산이 아니라
 *    입력 기본값이다.
 */
function toToday(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
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
      action={
        <Button variant="tertiary" size="compact" onClick={() => setMode("choose")}>
          추가
        </Button>
      }
    >
      {query.isPending ? (
        <SkeletonBlock label="안전 정보를 불러오는 중" />
      ) : isApiError(query.error, "consent_required") ? (
        // 🚨 일반 실패로 뭉뚱그리지 않는다 — 다시 시도해도 같은 403 이다.
        <ConsentRequiredCard childId={childId} error={query.error} what="안전 정보를 불러올 수 없고" />
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
        <HealthSafetyList childId={childId} items={items} />
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

      <HealthSafetySheet
        open={mode === "manual"}
        onClose={() => setMode(null)}
        childId={childId}
      />

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
