"use client";

import { josa } from "es-hangul";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { axisDayFormatter, formatDay, parseISODate } from "@/lib/format";
import type { GrowthLog } from "@/lib/api/types";

/**
 * 11-1 키 · 몸무게 상세 — 잰 값이 **이 아이 안에서** 어떻게 움직였는지.
 *
 * 🚨 **여기에 없는 것이 이 그래프의 절반이다.** 백분위 · 또래 곡선 · 표준 체중 · 예상선 ·
 *    "지난번보다 +2cm" 를 그리지 않는다. 그리는 순간 이 제품이 안 하기로 한 **발달 평가**가
 *    되고(최상위 CLAUDE.md §2 · 스펙 아웃), `DESIGN.md` 의 Don't("부모가 자기 아이를 지표로
 *    보게 하지 않는다")를 화면이 직접 어긴다. 여기 있는 것은 **보호자가 적은 값과 잰 날**뿐이고,
 *    그래프는 그 목록을 다른 방식으로 한 번 더 보여주는 것에 지나지 않는다.
 *    이 그래프가 목록 화면(11-1)에만 있고 프로필(11)에 없는 것도 같은 이유다 —
 *    매일 여는 화면에 곡선이 서면 그게 지표다.
 *
 * 🚨 **키와 몸무게를 한 그래프에 겹치지 않는다.** 단위도 자릿수도 달라서(104cm · 17kg) 겹치려면
 *    세로축이 둘이 되는데, 축이 둘이면 두 선이 만나고 갈라지는 자리가 **축을 어디서 끊었느냐**로
 *    정해진다. 없는 관계를 그림이 만들어 낸다 — 그래서 **작은 그래프 두 장**이다.
 *
 * 🚨 **곡선으로 잇지 않는다** (`type="linear"`). 부드러운 곡선은 잰 날 사이에도 값이 있었다고
 *    말하는데, 이 제품이 아는 것은 **점뿐**이다. 사이를 채우는 것은 추론이고 여긴 그 자리가 아니다.
 *
 * 🚨 **세로축을 값에 딱 맞춰 자르지 않는다.** 범위의 60% 를 위아래로 더 주고 최소 폭을 깔아서,
 *    1cm 차이가 화면 절반을 가로지르지 않게 한다. 축을 조이면 그림이 증감을 과장하고,
 *    그 과장이 곧 "우리 애가 덜 자랐나" 다.
 *
 * 🚨 **선에 브랜드색도 도메인색도 쓰지 않는다.** 측정은 Agent 도 영역도 아니라서
 *    (`GrowthLogList` 의 뉴트럴 타일과 같은 판단) 뉴트럴 잉크다. 선이 한 줄뿐이라 색이
 *    무엇을 가리킬 일도 없다 — 무엇의 그래프인지는 **위의 제목**이 글자로 진다.
 *
 * 🚨 **그림은 보조기술에 내보내지 않는다** (`aria-hidden`). 같은 데이터가 바로 아래 목록에
 *    글자로 서 있어서, 점 좌표를 읽어 주는 것은 중복이고 쓸모도 없다. 목록을 지우면
 *    이 판단이 깨진다 — 그래프만 남기지 말 것.
 */
export function GrowthChart({ logs }: { logs: GrowthLog[] }) {
  return (
    <div className="flex flex-col gap-4">
      <GrowthMeasureChart
        title="키"
        unit="cm"
        points={toPoints(logs, (log) => log.height_cm)}
        /** 1cm 범위여도 세로축을 최소 이만큼은 벌린다 (위 머리말). */
        minSpan={6}
      />
      <GrowthMeasureChart
        title="몸무게"
        unit="kg"
        points={toPoints(logs, (log) => log.weight_kg)}
        minSpan={3}
      />
    </div>
  );
}

/** 그래프 한 장이 그리는 점 하나. `at` 은 가로축용 타임스탬프다. */
type Point = { at: number; value: number; measured_on: string };

/**
 * 잰 값이 있는 날만 점이 된다 — 🚨 **한쪽만 잰 날의 빈 칸을 0 이나 직전 값으로 채우지 않는다.**
 * 0 이면 아이 몸무게가 0kg 이었다는 그림이 되고, 직전 값을 끌어오면 재지 않은 날을 잰 것처럼
 * 만든다. 그 날은 이 그래프에 **없는** 것이 맞다.
 *
 * 오름차순으로 세운다. 목록은 최신이 위지만(훑는 순서), 그래프의 왼쪽은 언제나 과거다.
 */
function toPoints(logs: GrowthLog[], pick: (log: GrowthLog) => number | null): Point[] {
  return logs
    .flatMap((log) => {
      const value = pick(log);
      const day = parseISODate(log.measured_on);
      if (value === null || day === null) return [];
      return [{ at: day.getTime(), value, measured_on: log.measured_on }];
    })
    .sort((a, b) => a.at - b.at);
}

function GrowthMeasureChart({
  title,
  unit,
  points,
  minSpan,
}: {
  title: string;
  unit: string;
  points: Point[];
  minSpan: number;
}) {
  /* 두 점 미만이면 그래프를 안 그리므로 아래 값들은 그때만 쓰인다 (빈 배열이어도 안전하다). */
  const first = points[0];
  const last = points[points.length - 1];
  const axis = verticalAxis(points, minSpan);

  return (
    <figure className="rounded-card border-line bg-surface m-0 border p-4">
      {/* 🚨 무엇의 그래프인지는 글자가 진다 — 선 색으로 구분하는 범례를 두지 않는다
          (선이 한 장에 하나뿐이라 범례가 가리킬 것도 없다). */}
      <figcaption className="text-label text-ink-muted">
        {title} · {unit}
      </figcaption>

      {points.length < 2 ? (
        /* 🚨 점 하나로 선을 그리지 않는다. 두 점이 있어야 "움직였다" 를 말할 수 있고,
           한 점짜리 그래프는 가로줄 하나를 변화처럼 보이게 한다. 대신 왜 아직 없는지를
           말한다 — 빈 상자를 남겨 두면 고장난 화면으로 읽힌다. */
        <p className="text-body-sm text-ink-muted mt-2">
          {/* 🚨 조사를 라벨에 박지 않는다 — `josa` 가 받침으로 고른다 (apps/web/CLAUDE.md §3).
              ⚠️ 옵션 키는 **받침형이 앞**이다(`"을/를"`). 뒤집으면 타입 에러다. */}
          {points.length === 0
            ? `아직 ${josa(title, "을/를")} 적은 날이 없어요.`
            : `${josa(title, "은/는")} 아직 한 번 잰 것만 있어요. 한 번 더 적으면 여기에 그려져요.`}
        </p>
      ) : (
        <div aria-hidden className="mt-3 h-40 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              {/* 눈금선은 뒤로 물러난다 — 가로만, `line` 1px. */}
              <CartesianGrid stroke="var(--color-line)" vertical={false} />
              <XAxis
                type="number"
                dataKey="at"
                scale="time"
                domain={["dataMin", "dataMax"]}
                /* 🚨 처음과 마지막 잰 날만 눈금으로 세운다. 점마다 날짜를 붙이면 360px 폭에서
                   글자가 겹치고, 겹친 눈금은 없는 눈금과 같다. 사이의 날짜는 점을 짚으면 나온다. */
                ticks={[first.at, last.at]}
                tickFormatter={axisDayFormatter(first.at, last.at)}
                tick={{ fill: "var(--color-ink-subtle)", fontSize: 12 }}
                tickLine={false}
                axisLine={{ stroke: "var(--color-line)" }}
                tickMargin={8}
                /* 양 끝 점이 축 밖으로 반쯤 잘리지 않게 안쪽으로 들인다. */
                padding={{ left: 10, right: 10 }}
              />
              <YAxis
                width={40}
                domain={axis.domain}
                ticks={axis.ticks}
                tickFormatter={axis.format}
                tick={{ fill: "var(--color-ink-subtle)", fontSize: 12 }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip
                content={<GrowthTooltip unit={unit} />}
                cursor={{ stroke: "var(--color-line-strong)", strokeDasharray: "3 3" }}
              />
              <Line
                type="linear"
                dataKey="value"
                stroke="var(--color-ink-muted)"
                strokeWidth={2}
                /* 점을 `surface` 테두리로 감싼다 — 선과 겹치는 자리에서 점이 선에 먹힌다. */
                dot={{
                  r: 4,
                  fill: "var(--color-ink-muted)",
                  stroke: "var(--color-surface)",
                  strokeWidth: 2,
                }}
                activeDot={{
                  r: 5,
                  fill: "var(--color-ink)",
                  stroke: "var(--color-surface)",
                  strokeWidth: 2,
                }}
                /* 🚨 등장 애니메이션을 만들지 않는다 (디자인 시스템 §8) — 하루에 여러 번
                   지친 상태로 여는 화면이라 100번째엔 매번 기다려야 하는 것이 된다. */
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </figure>
  );
}

/**
 * 세로축의 **눈금과 범위**.
 *
 * 🚨 **값에 딱 맞춰 자르지 않는다.** 잰 값의 범위에 30% 를 더하고(최소 `minSpan` 은 보장한다)
 *    거기서 축을 잡는다. 축을 조이면 1cm 차이가 화면 절반을 가로지르고, 그 과장이 곧
 *    "우리 애가 덜 자랐나" 다 (머리말).
 *
 * 🚨 **눈금 값을 recharts 에 맡기지 않는다.** 범위 끝이 91.7 · 107.1 같은 실제 값이라
 *    간격이 6 · 6 · 5 로 어긋나게 찍혔다 — 간격이 다른 눈금은 같은 거리를 다른 값으로
 *    읽게 만든다. 반올림한 간격(`NICE_STEPS`)을 골라 축을 그 배수에 맞추고 눈금을 직접 준다.
 */
function verticalAxis(
  points: Point[],
  minSpan: number,
): {
  domain: [number, number];
  ticks: number[];
  format: (value: number) => string;
} {
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 0);
  const pad = Math.max(span * 0.3, (minSpan - span) / 2);

  const step = niceStep(span + pad * 2);
  const low = Math.floor((min - pad) / step) * step;
  const high = Math.ceil((max + pad) / step) * step;

  const ticks: number[] = [];
  /* 부동소수 오차로 마지막 눈금이 빠지지 않게 여유를 준다 (0.5 간격이면 실제로 빠진다). */
  for (let value = low; value <= high + step / 1000; value += step) {
    ticks.push(round2(value));
  }

  return {
    domain: [round2(low), round2(high)],
    ticks,
    /* 간격이 1 미만일 때만 소수점을 남긴다 — "14.0" 은 잰 값이 아니라 눈금이다. */
    format: (value: number) => (step < 1 ? value.toFixed(1) : String(Math.round(value))),
  };
}

/** 눈금 간격 후보. 사람이 암산하는 수들이다 — 3.07 같은 간격은 읽는 데 품이 든다. */
const NICE_STEPS = [0.5, 1, 2, 5, 10, 20, 50, 100];

/** 축이 눈금 4칸 안에 들어오는 가장 작은 간격. 그래프 높이가 160px 이라 그 이상이면 빽빽하다. */
function niceStep(range: number): number {
  return NICE_STEPS.find((step) => range / step <= 4) ?? NICE_STEPS[NICE_STEPS.length - 1];
}

/** 0.1 + 0.2 문제를 눈금 값에서 없앤다 — 축에 `14.700000000000001` 이 찍히면 안 된다. */
function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * 점을 짚었을 때 나오는 한 줄. 🚨 **직전 값과의 차이를 여기서 만들지 않는다** — 그게
 * 증감이고, 이 화면이 안 그리기로 한 것이다 (머리말).
 */
function GrowthTooltip({
  unit,
  active,
  payload,
}: {
  unit: string;
  /* recharts 가 넘기는 값이다 — 우리가 부를 때는 비어 있다. */
  active?: boolean;
  payload?: Array<{ payload: Point }>;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;

  return (
    <div className="rounded-field border-line bg-surface border px-3 py-2 shadow-sm">
      <p className="text-body-sm text-ink">
        {point.value}
        {unit}
      </p>
      <p className="text-caption text-ink-subtle">{formatDay(point.measured_on)}</p>
    </div>
  );
}
