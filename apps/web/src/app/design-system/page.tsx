"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button, type ButtonVariant } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Chip, ChipRow } from "@/components/ui/chip";
import { DateField } from "@/components/ui/date-field";
import { DomainIcon, ICON_SIZE } from "@/components/ui/icon";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import type { Agent } from "@/lib/api/types";
import { contrastRatio, meetsAA, parseColor } from "./contrast";
import { COLOR_GROUPS, NOT_BUILT, TYPE_STEPS, type ColorPair } from "./tokens";

/**
 * 디자인 시스템을 한 화면에서 본다. 정본은 [`docs/web/design-system-v1.md`] 이고,
 * 이 페이지는 **그 문서가 코드에서 실제로 어떻게 나오는지**를 보여준다.
 *
 * 🚨 스크린샷이 아니라 **살아 있는 문서**다. 색은 `globals.css` 의 실제 변수를 읽고,
 *    대비비는 §10 의 공식으로 그 자리에서 계산한다. 토큰을 바꾸면 여기서 통과/실패가
 *    바뀌므로, 문서와 코드가 어긋나면 눈에 보인다.
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
          정본은 <code className="text-ink">docs/web/design-system-v1.md</code> 입니다. 이 화면은 그
          문서가 코드에서 실제로 어떻게 나오는지 보여줍니다. 대비비는 지금 값으로 계산합니다.
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
    </Section>
  );
}

/* ── 컴포넌트 ──────────────────────────────────────────────────────────── */

const BUTTON_VARIANTS: Array<{ variant: ButtonVariant; use: string }> = [
  { variant: "primary", use: "한 화면에 하나" },
  { variant: "secondary", use: "거절·취소. 거절은 파괴가 아니다" },
  { variant: "tertiary", use: "재시도 같은 약한 행동" },
  { variant: "approve", use: "🚨 승인 게이트 2곳 전용 · 52px 전체 폭" },
  { variant: "danger", use: "🚨 파괴적 확정에만 (동의 철회·삭제)" },
  { variant: "kakao", use: "00 로그인 전용 (외부 브랜드)" },
];

function ComponentSection() {
  const [sheet, setSheet] = useState<null | "normal" | "approval">(null);
  const [chip, setChip] = useState("공룡");
  const [date, setDate] = useState("");
  const [checked, setChecked] = useState(true);
  const [text, setText] = useState("");

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

      <SubTitle>카드</SubTitle>
      <Card>
        <p className="text-body-sm text-ink-muted">card — surface · line 1px · radius-card</p>
      </Card>
      <CardFailed>
        card-failed — 🚨 실패를 빨강으로 칠하지 않는다. danger 는 알레르기에만.
      </CardFailed>

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
      </div>
      <BottomSheet
        open={sheet !== null}
        onClose={() => setSheet(null)}
        dismissible={sheet !== "approval"}
        title={sheet === "approval" ? "승인 시트" : "일반 시트"}
        description={
          sheet === "approval"
            ? "스크림 탭·ESC 로 닫히지 않는다. 실수로 닫혀 draft 가 만료되는 경로를 만들지 않는다."
            : "스크림 탭·ESC 로 닫힌다."
        }
        footer={
          <Button block onClick={() => setSheet(null)}>
            닫기
          </Button>
        }
      >
        <p className="text-body-sm text-ink-muted">
          내용이 넘치면 화면이 아니라 시트 안에서만 스크롤한다. 최대 높이 88dvh.
        </p>
      </BottomSheet>

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
