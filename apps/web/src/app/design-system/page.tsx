"use client";

import {
  ArrowUp,
  CalendarDays,
  Camera,
  Mic,
  NotebookPen,
  ShieldCheck,
  Sprout,
  UserRound,
  Utensils,
} from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

import { DomainChip } from "@/components/domain-chip";
import { DayMarkLegend } from "@/components/month-grid";
import { Banner } from "@/components/ui/banner";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button, type ButtonVariant } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ChoiceField } from "@/components/ui/choice-field";
import { Chip, ChipRow, CountChip, EvidenceChip, EvidenceRow } from "@/components/ui/chip";
import { DateField } from "@/components/ui/date-field";
import { EmptyState } from "@/components/ui/empty-state";
import { IconButton } from "@/components/ui/icon-button";
import { IconTile } from "@/components/ui/icon-tile";
import { useToast } from "@/components/ui/toast";
import { DomainIcon, ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { ProgressSteps } from "@/components/ui/progress-steps";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Select } from "@/components/ui/select";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { TextArea } from "@/components/ui/text-area";
import { Tabs } from "@/components/ui/tabs";
import { TextInput } from "@/components/ui/text-input";
import type { Agent } from "@/lib/api/types";
import { contrastRatio, meetsAA, parseColor } from "./contrast";
import { SettingsGroup, SettingsInfoRow, SettingsLinkRow } from "@/components/settings-row";

import { COLOR_GROUPS, HEIGHT_TOKENS, NOT_BUILT, TYPE_STEPS, type ColorPair } from "./tokens";

/**
 * 디자인 시스템을 한 화면에서 본다. 의도와 금지 예는 [`docs/web/design-system-v1.md`],
 * 공통 값은 `globals.css`, 상태·포커스 동작은 컴포넌트 코드가 원본이고 (문서 머리말),
 * 이 페이지는 **그 셋이 실제로 어떻게 나오는지**를 보여주는 실행 가능한 예제다.
 *
 * 🚨 스크린샷이 아니라 **살아 있는 문서**다. 색·높이는 `globals.css` 의 실제 변수를 읽고,
 *    대비비는 §10 의 공식으로 그 자리에서 계산한다. 토큰을 바꾸면 여기서 통과/실패가
 *    바뀌므로, 문서와 코드가 어긋나면 눈에 보인다.
 *
 * 🚨 **"긴 문구" 는 글자를 키운 채로 본다** (문서 §10). 표의 값이 맞아도 문구가 잘리거나
 *    승인 버튼이 가려지면 사양이 지켜진 게 아니다.
 *
 * 폭은 `Screen` 을 그대로 쓴다 — 컴포넌트를 실제로 놓일 폭에서 봐야 판단이 맞는다.
 */
export default function DesignSystemPage() {
  return (
    <Screen className="gap-10">
      <header>
        <p className="text-label text-brand">내부 문서</p>
        <PageTitle className="mt-2">디자인 시스템</PageTitle>
        <p className="text-body text-ink-muted mt-3">
          의도는 <code className="text-ink">docs/web/design-system-v1.md</code>, 값은{" "}
          <code className="text-ink">globals.css</code> 가 원본입니다. 이 화면은 둘이 코드에서
          실제로 어떻게 나오는지 보여줍니다. 대비비와 높이는 지금 값으로 읽습니다.
        </p>
      </header>

      <ColorSection />
      <TypeSection />
      <ShapeSection />
      <ComponentSection />
      <MotionSection />
      <NotBuiltSection />
    </Screen>
  );
}

/* ── 섹션 틀 ───────────────────────────────────────────────────────────── */

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="border-line flex flex-col gap-4 border-t pt-8">
      <div>
        <h2 className="text-title text-ink">{title}</h2>
        {note ? <p className="text-body-sm text-ink-muted mt-1">{note}</p> : null}
      </div>
      {children}
    </section>
  );
}

function SubTitle({ children }: { children: ReactNode }) {
  return <h3 className="text-section text-ink">{children}</h3>;
}

/* ── 색 ────────────────────────────────────────────────────────────────── */

function ColorSection() {
  const root = useRef<HTMLDivElement>(null);
  const [values, setValues] = useState<Record<string, string>>({});

  // CSS 변수의 계산값은 렌더 뒤에만 읽을 수 있다.
  useEffect(() => {
    if (!root.current) return;
    const style = getComputedStyle(root.current);
    const names = new Set<string>();
    for (const group of COLOR_GROUPS) {
      for (const s of group.swatches) names.add(s);
      for (const p of group.pairs) {
        names.add(p.fg);
        names.add(p.bg);
      }
    }
    const next: Record<string, string> = {};
    for (const name of names) {
      next[name] =
        name === "white" ? "rgb(255, 255, 255)" : style.getPropertyValue(`--color-${name}`).trim();
    }
    setValues(next);
  }, []);

  const all = COLOR_GROUPS.flatMap((g) => g.pairs);
  const ratios = all.map((p) => ratioOf(p, values));
  const graded = all.filter((p) => p.kind !== "info");
  const failures = all.filter(
    (p, i) => p.kind !== "info" && ratios[i] !== null && !meetsAA(ratios[i]!, p.kind),
  );
  /**
   * 🚨 읽지 못한 쌍을 "통과" 로 세지 않는다. 처음에 그렇게 만들었다가, 색을 하나도 못 읽는
   *    상태에서 "전부 통과" 가 떴다 — 아무것도 검사하지 않는 검사가 됐다.
   */
  const unmeasured = all.filter((_, i) => ratios[i] === null);

  return (
    <div ref={root}>
      <Section
        title="색"
        note="본문 4.5:1 · 비텍스트 3:1 (§2 · §10). soft 배경 위 텍스트는 canvas 가 아니라 그 soft 색과 비교한다."
      >
        {Object.keys(values).length === 0 ? null : unmeasured.length > 0 ? (
          <CardFailed>
            <p className="text-ink">색을 읽지 못한 쌍이 {unmeasured.length}건 있습니다.</p>
            <p className="mt-1">
              토큰 이름이 바뀌었거나 파서가 그 색 형식을 모릅니다. 아래에서 &ldquo;읽기 실패&rdquo;
              로 표시된 줄입니다. 읽지 못한 것은 통과가 아닙니다.
            </p>
          </CardFailed>
        ) : failures.length > 0 ? (
          <CardFailed>
            <p className="text-ink">기준을 못 넘는 색 쌍이 {failures.length}건 있습니다.</p>
            <p className="mt-1">아래에서 &ldquo;미달&rdquo; 로 표시된 줄입니다.</p>
          </CardFailed>
        ) : (
          <Card>
            <p className="text-body text-ink">기준이 있는 {graded.length}쌍이 전부 통과합니다.</p>
            <p className="text-caption text-ink-subtle mt-1">
              지금 토큰 값으로 계산했습니다. 토큰을 바꾸면 이 결과도 바뀝니다.
              {all.length - graded.length > 0
                ? ` 기준 없는 값 ${all.length - graded.length}건은 숫자만 보여줍니다.`
                : ""}
            </p>
          </Card>
        )}

        {COLOR_GROUPS.map((group) => (
          <div key={group.title} className="flex flex-col gap-3">
            <SubTitle>{group.title}</SubTitle>

            {group.swatches.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {group.swatches.map((name) => (
                  <div key={name} className="flex items-center gap-2">
                    <span
                      className="border-line size-8 rounded-full border"
                      style={{ background: `var(--color-${name})` }}
                    />
                    <span className="text-caption text-ink-muted">
                      {name}
                      <br />
                      <span className="text-ink-subtle">{values[name] ?? ""}</span>
                    </span>
                  </div>
                ))}
              </div>
            ) : null}

            <ul className="flex flex-col gap-2">
              {group.pairs.map((pair) => (
                <li key={`${pair.fg}-on-${pair.bg}`}>
                  <PairRow pair={pair} values={values} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </Section>
    </div>
  );
}

function ratioOf(pair: ColorPair, values: Record<string, string>): number | null {
  const fg = parseColor(values[pair.fg] ?? "");
  const bg = parseColor(values[pair.bg] ?? "");
  if (!fg || !bg) return null;
  return contrastRatio(fg, bg);
}

function PairRow({ pair, values }: { pair: ColorPair; values: Record<string, string> }) {
  const ratio = ratioOf(pair, values);
  const pass = ratio === null || pair.kind === "info" ? null : meetsAA(ratio, pair.kind);

  return (
    <div className="border-line rounded-field flex items-center gap-3 border p-2">
      <span
        className="rounded-field flex h-11 w-16 shrink-0 items-center justify-center"
        style={{
          background: pair.bg === "white" ? "#fff" : `var(--color-${pair.bg})`,
          color: pair.fg === "white" ? "#fff" : `var(--color-${pair.fg})`,
        }}
      >
        {pair.kind === "text" ? (
          <span className="text-body-sm">가나 Aa</span>
        ) : (
          <span
            className="size-6 rounded-full border-2"
            style={{ borderColor: `var(--color-${pair.fg})` }}
          />
        )}
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="text-body-sm text-ink break-all">
          {pair.fg} <span className="text-ink-subtle">on</span> {pair.bg}
        </span>
        <span className="text-caption text-ink-subtle">
          {pair.note ? `${pair.note} · ` : ""}
          {pair.kind === "text"
            ? "본문 4.5:1"
            : pair.kind === "non-text"
              ? "비텍스트 3:1"
              : "기준 없음"}
        </span>
      </span>
      <span className="ml-auto shrink-0 text-right">
        <span className="text-body-sm text-ink tabular-nums">
          {ratio === null ? "—" : `${ratio.toFixed(2)}:1`}
        </span>
        <br />
        <span
          className={
            pass === false ? "text-caption text-danger-ink" : "text-caption text-ink-subtle"
          }
        >
          {pass === null ? (ratio === null ? "읽기 실패" : "참고") : pass ? "통과" : "미달"}
        </span>
      </span>
    </div>
  );
}

/* ── 타이포 ────────────────────────────────────────────────────────────── */

function TypeSection() {
  const root = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!root.current) return;
    const next: Record<string, string> = {};
    for (const step of TYPE_STEPS) {
      const el = root.current.querySelector(`[data-type="${step.name}"]`);
      if (!el) continue;
      const c = getComputedStyle(el);
      const size = parseFloat(c.fontSize);
      const lh = parseFloat(c.lineHeight) / size;
      const ls =
        c.letterSpacing === "normal"
          ? ""
          : ` · ${(parseFloat(c.letterSpacing) / size).toFixed(2)}em`;
      next[step.name] = `${size} / ${lh.toFixed(2)} · ${c.fontWeight}${ls}`;
    }
    setMeasured(next);
  }, []);

  return (
    <div ref={root}>
      <Section
        title="타이포"
        note="8단계 · 본문 16px / 1.6 · 학교안심 날개 R 자체 호스팅. 단일 웨이트라 600·700 은 브라우저 합성이고, 500 은 400 과 똑같이 나온다. 아래 '실측' 은 지금 화면에서 읽은 값이다."
      >
        <ul className="flex flex-col gap-4">
          {TYPE_STEPS.map((step) => (
            <li
              key={step.name}
              className="border-line flex flex-col gap-1 border-b pb-4 last:border-0"
            >
              <span data-type={step.name} className={`${step.cls} text-ink`}>
                지친 눈으로 읽는 글자
              </span>
              <span className="text-caption text-ink-subtle">
                {step.name} · {step.use}
              </span>
              <span className="text-caption text-ink-muted">
                표 {step.spec}
                <br />
                실측 {measured[step.name] ?? "…"}
              </span>
            </li>
          ))}
        </ul>

        <SubTitle>서체 두 벌</SubTitle>
        <p className="text-body-sm text-ink-muted">
          font-doc 은 폴백이 아니라 역할이다. 약관·처리방침처럼 읽고 동의해야 하는 긴 법률 문서는
          통째로 font-doc 을 쓰고, 한 화면에서 둘을 섞지 않는다.
        </p>
        <ul className="mt-3 flex flex-col gap-4">
          {[
            {
              cls: "font-sans",
              name: "font-sans",
              use: "기본값 · 화면 대부분",
              note: "학교안심 날개 R · Regular 400 하나뿐이라 아래 굵기는 합성이다",
            },
            {
              cls: "font-doc",
              name: "font-doc",
              use: "이용약관 · 개인정보 처리방침 · 동의 전문",
              note: "Pretendard Variable · 굵기가 실제 웨이트로 나온다",
            },
          ].map((f) => (
            <li
              key={f.name}
              className="border-line flex flex-col gap-1 border-b pb-4 last:border-0"
            >
              <span className={`${f.cls} text-body text-ink`}>
                제3자 제공에 동의하지 않을 수 있으며, 동의를 거부해도 서비스 이용에 제한이 없습니다.
              </span>
              <span className={`${f.cls} text-section text-ink`}>수집·이용 목적 (굵기 600)</span>
              <span className="text-caption text-ink-subtle">
                {f.name} · {f.use}
              </span>
              <span className="text-caption text-ink-muted">{f.note}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

/* ── 모양 ──────────────────────────────────────────────────────────────── */

function ShapeSection() {
  return (
    <Section
      title="모양 · 간격"
      note="radius 4개 · 그림자 1개 · 간격은 4px 배수. 이 개수를 늘리지 않는다."
    >
      <SubTitle>radius</SubTitle>
      <div className="flex flex-wrap items-end gap-3">
        {[
          { cls: "rounded-field", label: "field 10", use: "입력·버튼·배너" },
          { cls: "rounded-card", label: "card 14", use: "카드" },
          { cls: "rounded-sheet", label: "sheet 20", use: "바텀시트 상단" },
          { cls: "rounded-full", label: "full", use: "칩·탭·아바타" },
        ].map((r) => (
          <div key={r.cls} className="flex flex-col items-center gap-1">
            <span className={`bg-brand-soft border-brand size-14 border ${r.cls}`} />
            <span className="text-caption text-ink-muted">{r.label}</span>
            <span className="text-caption text-ink-subtle">{r.use}</span>
          </div>
        ))}
      </div>

      <SubTitle>높낮이</SubTitle>
      <div className="flex flex-col gap-2">
        <div className="border-line rounded-card bg-surface text-body-sm text-ink-muted border p-4">
          평면 — 화면의 95%. 경계는 그림자가 아니라 line 1px 이 만든다.
        </div>
        <div className="rounded-sheet bg-surface shadow-sheet text-body-sm text-ink-muted p-4">
          떠 있음 — 바텀시트 하나뿐. 단계를 늘리지 않는다.
        </div>
      </div>

      <SubTitle>간격</SubTitle>
      <ul className="flex flex-col gap-2">
        {[
          { v: 4, n: "2xs", use: "아이콘과 라벨 사이" },
          { v: 8, n: "xs", use: "칩 사이" },
          { v: 12, n: "sm", use: "카드와 카드 사이" },
          { v: 16, n: "md", use: "카드 안쪽·화면 좌우" },
          { v: 20, n: "lg", use: "시트 안쪽" },
          { v: 24, n: "xl", use: "섹션 사이" },
          { v: 32, n: "2xl", use: "화면 상단·빈 상태" },
        ].map((s) => (
          <li key={s.n} className="flex items-center gap-3">
            <span className="bg-brand h-3 rounded-full" style={{ width: s.v }} />
            <span className="text-caption text-ink-muted">
              {s.n} {s.v}px · {s.use}
            </span>
          </li>
        ))}
      </ul>

      <SubTitle>높이</SubTitle>
      <p className="text-caption text-ink-subtle">
        globals.css 의 --height-* 를 지금 읽은 값이다. 🚨 전부 최소 높이(min-h-*)로만 쓴다 — 문구가
        길거나 글자를 키우면 늘어난다.
      </p>
      <HeightList />
    </Section>
  );
}

/** 높이 토큰을 문서에 숫자로 옮겨 적지 않고 여기서 읽는다 — 값의 원본은 한 곳이다. */
function HeightList() {
  const root = useRef<HTMLUListElement>(null);
  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!root.current) return;
    const style = getComputedStyle(root.current);
    const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize);
    const next: Record<string, string> = {};
    for (const { name } of HEIGHT_TOKENS) {
      const raw = style.getPropertyValue(`--height-${name}`).trim();
      // rem 은 루트 글자 크기를 따라간다. 브라우저 기본 글자를 키우면 px 이 같이 커지는 게 맞다.
      const px = raw.endsWith("rem") ? Math.round(parseFloat(raw) * rootPx) : null;
      next[name] = raw ? `${raw}${px === null ? "" : ` · ${px}px`}` : "읽기 실패";
    }
    setValues(next);
  }, []);

  return (
    <ul ref={root} className="flex flex-col gap-2">
      {HEIGHT_TOKENS.map((t) => (
        <li key={t.name} className="flex items-center gap-3">
          <span
            className="bg-brand-soft border-brand w-3 shrink-0 rounded-sm border"
            style={{ height: `var(--height-${t.name})` }}
          />
          <span className="text-caption text-ink-muted">
            {t.name} {values[t.name] ?? "…"} · {t.use}
          </span>
        </li>
      ))}
    </ul>
  );
}

/* ── 컴포넌트 ──────────────────────────────────────────────────────────── */

const BUTTON_VARIANTS: Array<{ variant: ButtonVariant; use: string }> = [
  { variant: "primary", use: "한 화면에 하나" },
  { variant: "secondary", use: "거절·취소. 거절은 파괴가 아니다" },
  { variant: "tertiary", use: "재시도 같은 약한 행동" },
  { variant: "approve", use: "🚨 승인 게이트 2곳 전용 · 전체 폭" },
  { variant: "danger", use: "🚨 파괴적 확정에만 (동의 철회·삭제)" },
  { variant: "kakao", use: "00 로그인 전용 (외부 브랜드)" },
];

const DS_GENDER_OPTIONS = [
  { value: "male", label: "남자아이" },
  { value: "female", label: "여자아이" },
] as const;

const DS_DOMAIN_OPTIONS = [
  { value: "all", label: "전체" },
  { value: "food", label: "식사" },
  { value: "activity", label: "놀이" },
] as const;

const DS_STATE_OPTIONS = [
  { value: "all", label: "전체" },
  { value: "confirmed", label: "확인됨" },
  { value: "candidate", label: "후보" },
] as const;

function ComponentSection() {
  const toast = useToast();
  const [sheet, setSheet] = useState<null | "normal" | "approval" | "document">(null);
  const [dsGender, setDsGender] = useState("male");
  const [selectDomain, setSelectDomain] = useState("all");
  const [selectState, setSelectState] = useState("confirmed");
  const [chip, setChip] = useState("공룡");
  const [date, setDate] = useState("");
  const [checked, setChecked] = useState(true);
  const [text, setText] = useState("");
  const [area, setArea] = useState("");
  const [bar, setBar] = useState("");

  return (
    <Section title="컴포넌트" note="지금 코드에 있는 것만. 사양은 §7, 없는 것은 맨 아래에.">
      <SubTitle>버튼</SubTitle>
      <ul className="flex flex-col gap-3">
        {BUTTON_VARIANTS.map(({ variant, use }) => (
          <li key={variant} className="flex flex-col gap-1">
            <Button variant={variant} block={variant === "approve"}>
              {variant}
            </Button>
            <span className="text-caption text-ink-subtle">{use}</span>
          </li>
        ))}
        <li className="flex flex-col gap-1">
          <Button disabled>disabled</Button>
          <span className="text-caption text-ink-subtle">
            브랜드색을 흐리게 만들지 않는다 — surface-muted + ink-subtle
          </span>
        </li>
        <li className="flex flex-col gap-1">
          <Button disabled>
            <Spinner />
            기다리는 중
          </Button>
          <span className="text-caption text-ink-subtle">
            Spinner — prefers-reduced-motion 에서는 숨고 문구만 남는다
          </span>
        </li>
      </ul>

      <SubTitle>아이콘 버튼</SubTitle>
      <p className="text-caption text-ink-subtle">
        글자 없는 원형 버튼. 🚨 aria-label 이 필수고, brand 의 비활성 배경은 surface 다 —
        채팅바(surface-muted) 위에서 같은 색이면 버튼이 사라진다.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <IconButton label="사진으로 적기">
          <Camera aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </IconButton>
        <IconButton label="말로 적기" disabled>
          <Mic aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </IconButton>
        <IconButton label="보내기" tone="brand">
          <ArrowUp aria-hidden size={ICON_SIZE.md} strokeWidth={2} />
        </IconButton>
        <IconButton label="보내기 (비활성)" tone="brand" disabled>
          <ArrowUp aria-hidden size={ICON_SIZE.md} strokeWidth={2} />
        </IconButton>
      </div>

      <SubTitle>아이콘 타일</SubTitle>
      <p className="text-caption text-ink-subtle">
        brand-soft 바탕 + brand-ink 아이콘. 목록 줄 앞에 선다. 🚨 도메인 색을 여기 넣지 않는다.
        neutral 톤(surface-muted + ink-muted)은 브랜드색도 도메인색도 못 쓰는 목록용이다 (07
        기록·기억) — 거기서는 색이 아니라 배치가 종류를 말한다.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <IconTile icon={Utensils} />
        <IconTile icon={CalendarDays} />
        <IconTile icon={Sprout} />
        <IconTile icon={Utensils} tone="neutral" />
        <IconTile icon={CalendarDays} tone="neutral" />
      </div>

      <SubTitle>채팅바 (03 홈)</SubTitle>
      <p className="text-caption text-ink-subtle">
        surface-muted 알약 안에 사진 · 마이크 · 입력 · 보내기. 입력은 테두리 없는 bare 변형이고,
        상자처럼 보이는 것은 알약 쪽이다. 화면 하단 고정은 Screen 의 bottomBar 가 소유한다.
      </p>
      <div className="bg-surface-muted flex items-end gap-1 rounded-full p-1">
        <IconButton label="사진으로 적기" className="self-center">
          <Camera aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </IconButton>
        <div className="min-w-0 flex-1">
          <TextArea
            label="채팅바 예시"
            labelHidden
            variant="bare"
            maxHeightPx={104}
            placeholder="오늘 있었던 일, 말하듯 적어주세요"
            value={bar}
            onChange={(e) => setBar(e.target.value)}
          />
        </div>
        <IconButton
          label="보내기"
          tone="brand"
          className="self-center"
          disabled={bar.trim().length === 0}
        >
          <ArrowUp aria-hidden size={ICON_SIZE.md} strokeWidth={2} />
        </IconButton>
      </div>

      <SubTitle>입력</SubTitle>
      <TextInput
        label="라벨"
        hint="왜 묻는지를 여기에 쓴다."
        placeholder="placeholder"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <TextInput label="에러 상태" value="" onChange={() => {}} error="사유를 한 줄로 쓴다." />
      <DateField
        label="날짜"
        hint="달력은 바텀시트로 연다 — 좁은 폭에서 팝오버는 화면을 벗어난다."
        value={date}
        onChange={setDate}
        fromDate={new Date(new Date().getFullYear() - 20, 0, 1)}
        toDate={new Date()}
      />
      <Checkbox
        checked={checked}
        onChange={setChecked}
        label="체크박스"
        description="표식은 rounded-full — 24px 에 새 radius 를 만들지 않으려고 골랐다."
      />
      <TextArea
        label="여러 줄 입력 (03 홈)"
        hint="최소 96 · 자동 증가 · 5줄이 넘으면 안에서 스크롤한다."
        placeholder="예: 저녁에 계란말이를 또 찾았어요"
        value={area}
        onChange={(e) => setArea(e.target.value)}
      />

      <SubTitle>칩</SubTitle>
      <ChipRow>
        {["공룡", "자동차", "블록"].map((c) => (
          <Chip key={c} selected={chip === c} onClick={() => setChip(c)}>
            {c}
          </Chip>
        ))}
        <Chip selected={false} disabled onClick={() => {}}>
          비활성
        </Chip>
      </ChipRow>
      <p className="text-caption text-ink-subtle">
        도메인 칩 · 근거 칩 · 건수. 🚨 한 화면에 도메인 색은 2개까지다 — 여기는 네 색을 나란히
        비교하려고 모아 둔 내부 문서라 예외다.
      </p>
      <EvidenceRow>
        {(["food", "activity", "education", "health"] as Agent[]).map((agent) => (
          <DomainChip key={agent} agent={agent} />
        ))}
      </EvidenceRow>
      <EvidenceRow>
        <EvidenceChip label="계란 반찬" meta="3일 전" />
        <EvidenceChip label="물놀이" stale />
        <CountChip>외 2건</CountChip>
      </EvidenceRow>

      <SubTitle>긴 문구</SubTitle>
      <p className="text-caption text-ink-subtle">
        🚨 브라우저 확대 200% 와 시스템 글자 크기를 키운 상태로 본다. 글자가 버튼·칩 밖으로 나가거나
        잘리면 안 되고, 승인 버튼은 문구 전체가 보여야 한다. 높이 값이 맞아도 여기서 깨지면 사양이
        지켜진 게 아니다.
      </p>
      <Button block>이번 주 토요일 오전 10시 소아과 예방접종 일정을 캘린더에 추가하기</Button>
      <Button variant="approve">
        땅콩 알레르기를 아이의 건강 기록으로 확정하고 앞으로 식사 추천에서 계속 빼기
      </Button>
      <ChipRow>
        <Chip selected={false} onClick={() => {}}>
          블록 쌓기와 종이컵 탑 같은 높이 쌓는 놀이
        </Chip>
      </ChipRow>

      <SubTitle>카드</SubTitle>
      <Card>
        <p className="text-body-sm text-ink-muted">card — surface · line 1px · radius-card</p>
      </Card>
      <Card tone="accent">
        <p className="text-body-sm text-ink-muted">
          card-accent — 테두리만 brand. 🚨 한 화면에 한 장. 두 장이면 강조가 아니라 장식이다.
        </p>
      </Card>
      <CardFailed>
        card-failed — 🚨 실패를 빨강으로 칠하지 않는다. danger 는 알레르기에만.
      </CardFailed>

      <SubTitle>탭 (07)</SubTitle>
      {/* 링크는 이 화면 안의 앵커다 — 내부 문서에서 다른 화면으로 새 나가지 않게. */}
      <div id="design-system-tabs">
        <Tabs
          items={[
            { key: "a", label: "기록", href: "#design-system-tabs" },
            { key: "b", label: "기억", href: "#design-system-tabs" },
            { key: "c", label: "제안 피드백", href: "#design-system-tabs" },
          ]}
          active="a"
          label="탭 예시"
        />
      </div>
      <p className="text-caption text-ink-subtle">
        활성은 ink + brand 2px 밑줄입니다. 🚨 굵기로 구분하지 않습니다 — 본문 서체가 단일 웨이트라
        label 500 과 600 이 화면에서 같습니다 (§4). 전환은 URL 에 남깁니다.
      </p>

      <SubTitle>캘린더 표식 (09)</SubTitle>
      <DayMarkLegend hasProfileMarks />
      <p className="text-caption text-ink-subtle">
        🚨 색이 아니라 모양으로 가릅니다. 네 표식은 전부 currentColor 라 고른 날(brand 채움)
        위에서도 같은 모양이 읽힙니다. 뜻을 잇는 것은 이 범례고, 날짜 칸의 aria-label 이 같은 말을
        다시 합니다.
      </p>

      <SubTitle>배너</SubTitle>
      <p className="text-caption text-ink-subtle">
        🚨 caution 은 승인 게이트 2곳 전용, danger 는 알레르기·건강 중단·파괴적 확정 전용이다. 화면
        최상단 한 곳에만 두고, 둘이 동시에 필요하면 danger 가 이긴다.
      </p>
      <Banner tone="caution" title="처음 보는 재료가 있어요">
        아이 알레르기 기록에 없는 재료예요. 보호자가 확인해 주셔야 넣을 수 있어요.
      </Banner>
      <Banner tone="danger" title="알레르기 기록에 추가했어요">
        이 재료가 들어간 제안은 넣지 않아요.
      </Banner>

      <SubTitle>고르기 상자</SubTitle>
      <p className="text-caption text-ink-subtle">
        직접 만든 드롭다운이다 — 네이티브 &lt;select&gt; 는 닫혀 있을 때 말고는 생김새를 우리가 못
        정해서 쓰지 않는다. 대신 접근성이 전부 우리 책임이다: combobox + listbox ARIA · 열 때 고른
        항목으로 포커스가 들어가고 닫을 때 버튼으로 돌아온다 · ESC · 바깥 클릭 · Tab · 스크롤에
        닫힌다 · 방향키 · Home · End. 🚨 그림자 없이 line-strong 1px 로 뜬 면을 만들고(§6), 등장
        애니메이션도 쉐브론 회전도 없다(§8). 🚨 라벨을 지우지 않는다.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <Select
          label="분류"
          value={selectDomain}
          options={DS_DOMAIN_OPTIONS}
          onChange={setSelectDomain}
        />
        <Select
          label="상태"
          value={selectState}
          options={DS_STATE_OPTIONS}
          onChange={setSelectState}
        />
      </div>

      <SubTitle>고르는 칸 (둘 중 하나)</SubTitle>
      <p className="text-caption text-ink-subtle">
        반드시 하나를 고르는 칸이다. 🚨 선택지가 둘이면 드롭다운을 쓰지 않는다 — 있는 선택지를 상자
        안에 감췄다가 탭 두 번으로 다시 보여줄 뿐이다. 🚨 chip-choice 도 아니다: 칩은 aria-pressed
        토글이라 둘 다 꺼진 상태가 정상이고, 보조기술에 &quot;둘 중 하나&quot; 라는 관계가 안
        드러난다. 그래서 네이티브 라디오를 sr-only 로 숨기고 표식만 그린다 — 그룹 · 방향키 이동 · 단
        하나만 선택을 브라우저가 준다. 🚨 고른 것을 색 하나로 말하지 않는다(체크 아이콘 · §3) · 칸을
        똑같이 나눈다(한쪽이 넓으면 기본값처럼 보인다) · min-h-field 로 같은 폼의 입력과 높이를
        맞춘다.
      </p>
      <ChoiceField
        label="성별"
        value={dsGender}
        options={DS_GENDER_OPTIONS}
        onChange={setDsGender}
      />

      <SubTitle>토스트</SubTitle>
      <p className="text-caption text-ink-subtle">
        화면 안에 자리가 없는 사실을 잠깐 띄운다. 🚨 성공을 알리지 않고(성공은 화면이 이미 말한다),
        되돌릴 것이 있으면 여기 담지 않는다(사라지는 자리다). 남는 자리는 **조용히 되돌아간 실패**
        하나 — 준비물 체크처럼 낙관적으로 반영했다가 실패해서 원래대로 돌아가는 경우다. 위에
        붙고(아래는 채팅바+이동 바), 그림자·애니메이션이 없고, 한 번에 하나이며 6초 뒤 사라진다. 🚨
        바텀시트 안에서 부르지 않는다 — dialog 의 top layer 뒤로 깔려 안 보인다.
      </p>
      <div>
        <Button
          variant="secondary"
          onClick={() => toast.show("준비물 체크를 저장하지 못했어요. 잠시 뒤에 다시 눌러주세요.")}
        >
          토스트 띄우기
        </Button>
      </div>

      <SubTitle>바텀시트</SubTitle>
      <p className="text-caption text-ink-subtle">
        네이티브 &lt;dialog&gt; 위에 얹는다 — 포커스 트랩 · ESC · 바깥 inert · 스크림을 브라우저가
        준다. 🚨 dismissible: false 는 승인 시트 전용이다.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => setSheet("normal")}>
          일반 시트
        </Button>
        <Button variant="secondary" onClick={() => setSheet("approval")}>
          승인 시트 (안 닫힘)
        </Button>
        <Button variant="secondary" onClick={() => setSheet("document")}>
          문서 시트 (font-doc)
        </Button>
      </div>
      <BottomSheet
        open={sheet !== null}
        onClose={() => setSheet(null)}
        dismissible={sheet !== "approval"}
        variant={sheet === "document" ? "document" : "default"}
        title={
          sheet === "approval"
            ? "승인 시트"
            : sheet === "document"
              ? "서비스 이용약관"
              : "일반 시트"
        }
        description={
          sheet === "approval"
            ? "스크림 탭·ESC 로 닫히지 않는다. 실수로 닫혀 draft 가 만료되는 경로를 만들지 않는다."
            : sheet === "document"
              ? "제목까지 통째로 font-doc 이다 — 한 화면 한 서체."
              : "스크림 탭·ESC 로 닫힌다."
        }
        footer={
          <Button block onClick={() => setSheet(null)}>
            닫기
          </Button>
        }
      >
        <p className="text-body-sm text-ink-muted">
          {sheet === "document"
            ? "약관·동의 전문 전용이다. 제목만 손글씨로 남기면 size-adjust 때문에 같은 시트 안에서 글자 크기감이 어긋난다."
            : "내용이 넘치면 화면이 아니라 시트 안에서만 스크롤한다. 최대 높이 88dvh."}
        </p>
      </BottomSheet>

      <SubTitle>설정 줄 (10)</SubTitle>
      <p className="text-caption text-ink-subtle">
        한 덩어리 안에 line 1px 로 줄을 나눈다. 🚨 줄마다 카드를 두르지 않는다 (카드 속 카드). 🚨
        상태를 글자로 단다 — 색·아이콘으로 대신하지 않는다. 되돌리기 어려운 행동이 달린 줄은 줄
        전체를 버튼으로 만들지 않는다.
      </p>
      <SettingsGroup>
        <SettingsLinkRow href="#" icon={UserRound} title="다른 화면으로 가는 줄" />
        <SettingsInfoRow
          icon={ShieldCheck}
          title="행동이 따로 달린 줄"
          status="동의함"
          note="9월 5일 (토)에 동의했어요"
          action={
            <Button variant="tertiary" size="compact">
              철회하기
            </Button>
          }
        />
      </SettingsGroup>

      <SubTitle>진행 오버레이 (04 · SSE)</SubTitle>
      <p className="text-caption text-ink-subtle">
        완료 · 진행 중 · 대기 3상태. 단계 문구는 서버의 step.label 을 그대로 쓴다. 🚨 20초를 넘기면
        오버레이를 걷고 부분 결과로 넘어간다 (NF-06).
      </p>
      <Card>
        <ProgressSteps index={2} total={3} label="기록을 나누고 있어요" />
      </Card>

      <SubTitle>빈 상태 · 스켈레톤</SubTitle>
      <p className="text-caption text-ink-subtle">
        🚨 빈 상태를 사과문으로 쓰지 않는다. 쌓인 기록 건수를 그대로 보여준다.
      </p>
      <EmptyState
        icon={NotebookPen}
        title="아래에 한 줄 적으면 여기에 쌓여요"
        description="기록이 없으면 제안도 만들지 않아요."
        count={0}
      />
      <Card>
        <SkeletonBlock />
      </Card>

      <SubTitle>아이콘 · 도메인</SubTitle>
      <p className="text-caption text-ink-subtle">
        크기{" "}
        {Object.entries(ICON_SIZE)
          .map(([k, v]) => `${k} ${v}`)
          .join(" · ")}{" "}
        · strokeWidth 1.75 고정. 🚨 DomainIcon 은 aria-hidden 이라 의미는 옆의 라벨이 진다.
      </p>
      <ul className="flex flex-col gap-2">
        {(["food", "activity", "education", "health"] as Agent[]).map((agent) => (
          <li key={agent} className="flex items-center gap-3">
            <span
              className={`flex size-10 items-center justify-center rounded-full ${softOf(agent)}`}
            >
              <DomainIcon agent={agent} size="md" />
            </span>
            <span className="text-body-sm text-ink">{agent}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function softOf(agent: Agent): string {
  return {
    food: "bg-food-soft text-food-ink",
    activity: "bg-activity-soft text-activity-ink",
    education: "bg-education-soft text-education-ink",
    health: "bg-health-soft text-health-ink",
  }[agent];
}

/* ── 모션 ──────────────────────────────────────────────────────────────── */

/**
 * 미디어쿼리를 구독한다. 서버에는 `matchMedia` 가 없어서 첫 렌더는 `null` 이고,
 * `useSyncExternalStore` 라 effect 안에서 setState 를 하지 않는다 (그러면 렌더가 한 번 더 돈다).
 */
function useMediaQuery(query: string): boolean | null {
  return useSyncExternalStore(
    (onChange) => {
      const mql = matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => matchMedia(query).matches,
    () => null,
  );
}

function MotionSection() {
  const hoverable = useMediaQuery("(hover: hover)");
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");

  return (
    <Section
      title="모션 · 상호작용"
      note="fast 120ms · base 200ms · sheet 280ms. 색만 바꾸고 크기·위치는 건드리지 않는다."
    >
      <Card>
        <p className="text-body-sm text-ink">지금 이 기기</p>
        <p className="text-caption text-ink-muted mt-2">
          hover 가능:{" "}
          {hoverable === null
            ? "…"
            : hoverable
              ? "예 — hover 상태가 보입니다"
              : "아니오 — 터치 기기라 hover 를 걸지 않습니다"}
          <br />
          동작 줄이기: {reduced === null ? "…" : reduced ? "켜짐 — 스피너가 숨습니다" : "꺼짐"}
        </p>
        <p className="text-caption text-ink-subtle mt-2">
          🚨 hover 는 @media (hover: hover) 안에서만 겁니다. 터치에서 탭 뒤에 눌러붙는 것을
          막습니다. 웹뷰에는 hover 가 없으므로 누른 느낌은 active 가 맡습니다.
        </p>
      </Card>
      <p className="text-body-sm text-ink-muted">
        위 버튼들에 커서를 올리거나 눌러 보세요. 등장·스크롤 애니메이션은 일부러 만들지 않습니다 —
        하루에 여러 번 지친 상태로 여는 화면이라 100번째에는 매번 기다려야 하는 것이 됩니다.
      </p>
    </Section>
  );
}

/* ── 아직 없는 것 ──────────────────────────────────────────────────────── */

function NotBuiltSection() {
  return (
    <Section
      title="§7 에 사양은 있는데 아직 없는 것"
      note="이 목록이 비면 컴포넌트가 다 만들어진 것이다."
    >
      <ul className="flex flex-col gap-3">
        {NOT_BUILT.map((item) => (
          <li key={item.name} className="border-line rounded-field border border-dashed p-3">
            <p className="text-body-sm text-ink">{item.name}</p>
            <p className="text-caption text-ink-subtle mt-1">
              {item.where}
              {item.why ? ` · ${item.why}` : ""}
            </p>
          </li>
        ))}
      </ul>
    </Section>
  );
}
