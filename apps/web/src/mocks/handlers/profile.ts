import { http, HttpResponse } from "msw";

import type { ChildProfile, GrowthLog, UpdateChildRequest } from "@/lib/api/types";

import { childProfile, growthLogs, measuredLabel, newGrowthLog } from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";

/**
 * 11 아이 프로필 — 별명·생일·성별과 키·몸무게 측정 로그.
 *
 * ⚠️ **네 경로 전부 계약서 v1 에 없다** (이슈 #75). 계약서가 주는 것은
 *    `PATCH /children/{cid}` 의 `nickname` · `birth_date` 둘뿐이고, 읽는 엔드포인트도
 *    성별도 측정 로그도 없다. 키·몸무게·성별은 최상위 `CLAUDE.md` §2 의 수집 범위
 *    ("이름(별명)·나이·알레르기 여부까지")도 넘는다 — PM 결정과 계약 확정 전까지
 *    **이 파일이 그 세 엔드포인트의 유일한 구현**이다.
 *
 * 🚨 이 주석을 지우고 실서버에 붙이지 않는다. 지워지면 계약서에 있는 것처럼 보인다.
 *
 * 🚨 **측정 로그는 승인 게이트가 아니다.** 잘못 적으면 그 줄을 지우면 되니 되돌릴 수 있고,
 *    승인 게이트는 2곳에서 늘리지 않는다 (최상위 §2). 그래서 `withIdempotency` 로 감싸지
 *    않는다 — 되돌릴 수 없는 5개(`lib/api/idempotency.ts` 표)에 이 경로가 없다.
 */

/** 목은 프로세스 수명만큼 산다 — 고친 값이 다음 GET 에 보여야 화면을 확인할 수 있다. */
let profile: ChildProfile = { ...childProfile };
let logs: GrowthLog[] = [...growthLogs];

export function resetProfileState(): void {
  profile = { ...childProfile };
  logs = [...growthLogs];
}

/** `empty` 시나리오는 "아직 아무것도 안 적은 계정" 이다 — 측정 기록이 0건이어야 한다. */
function currentLogs(): GrowthLog[] {
  return currentScenario() === "empty" ? [] : logs;
}

export const profileHandlers = [
  http.get(url("/children/:cid"), async () => {
    await networkDelay();
    return HttpResponse.json(profile);
  }),

  /**
   * 🚨 **세 값 다 선택이다.** 화면은 고친 것만 보낸다 — 안 고친 필드를 함께 올리면
   *    두 보호자가 같은 화면을 열어 뒀을 때 나중 저장이 남의 수정을 덮는다.
   */
  http.patch(url("/children/:cid"), async ({ request }) => {
    await networkDelay(320);
    const body = (await request.json()) as UpdateChildRequest;

    if (body.nickname !== undefined && body.nickname.trim() === "") {
      return apiError(422, "validation_failed", "별명을 적어주세요");
    }

    profile = {
      ...profile,
      ...(body.nickname !== undefined ? { nickname: body.nickname.trim() } : {}),
      ...(body.birth_date !== undefined ? { birth_date: body.birth_date } : {}),
      ...(body.gender !== undefined ? { gender: body.gender } : {}),
    };

    return HttpResponse.json({ child: profile });
  }),

  http.get(url("/children/:cid/growth"), async () => {
    await networkDelay();
    return HttpResponse.json({ items: currentLogs(), next_cursor: null });
  }),

  /**
   * 🚨 **둘 다 비어 있으면 거부한다.** 한쪽만 재고 오는 날이 있어서 각각 optional 이지만,
   *    둘 다 없는 행은 "잰 날짜만 있고 잰 것이 없는" 기록이라 목록에서 뜻이 없다.
   */
  http.post(url("/children/:cid/growth"), async ({ request }) => {
    await networkDelay(320);
    const body = (await request.json()) as {
      measured_on: string;
      height_cm?: number | null;
      weight_kg?: number | null;
    };

    if (body.height_cm == null && body.weight_kg == null) {
      return apiError(422, "validation_failed", "키나 몸무게 중 하나는 적어주세요");
    }

    const log = newGrowthLog(body);
    logs = [log, ...logs];

    return HttpResponse.json({ log }, { status: 201 });
  }),

  /**
   * 🚨 **보낸 필드만 바꾼다** (`PATCH /children/{cid}` 와 같은 규칙). 안 고친 필드를 함께
   *    올리면 두 보호자가 같은 화면을 열어 뒀을 때 나중 저장이 남의 수정을 덮는다.
   *
   * 🚨 **`null` 은 "그날 그건 안 잰 것으로 되돌리기" 다.** 키를 안 보내는 것(그대로 두기)과
   *    구분된다. 다만 **고친 결과가 둘 다 비면 거부한다** — 잰 날짜만 있고 잰 것이 없는
   *    기록은 목록에서 뜻이 없다 (등록과 같은 판단).
   *
   * 🚨 **`measured_label` 은 서버가 다시 만든다.** 날짜를 고치면 "2주 전" 도 같이 바뀌어야
   *    하는데, 프론트가 만들면 규칙이 두 곳이 된다 (최상위 CLAUDE.md §3).
   */
  http.patch(url("/children/:cid/growth/:id"), async ({ request, params }) => {
    await networkDelay(320);
    const body = (await request.json()) as {
      measured_on?: string;
      height_cm?: number | null;
      weight_kg?: number | null;
    };

    const current = logs.find((log) => log.id === params.id);
    if (!current) return apiError(404, "not_found", "이미 지워진 기록이에요");

    const next = {
      ...current,
      ...(body.measured_on !== undefined ? { measured_on: body.measured_on } : {}),
      ...(body.height_cm !== undefined ? { height_cm: body.height_cm } : {}),
      ...(body.weight_kg !== undefined ? { weight_kg: body.weight_kg } : {}),
    };

    if (next.height_cm == null && next.weight_kg == null) {
      return apiError(422, "validation_failed", "키나 몸무게 중 하나는 적어주세요");
    }

    next.measured_label = measuredLabel(next.measured_on);
    logs = logs.map((log) => (log.id === next.id ? next : log));

    return HttpResponse.json({ log: next });
  }),

  /** 되돌릴 수 있는 것이라 확인 단계도 승인 게이트도 없다 — 목록에서 바로 지운다. */
  http.delete(url("/children/:cid/growth/:id"), async ({ params }) => {
    await networkDelay();
    const before = logs.length;
    logs = logs.filter((log) => log.id !== params.id);
    if (logs.length === before) {
      return apiError(404, "not_found", "이미 지워진 기록이에요");
    }
    return new HttpResponse(null, { status: 204 });
  }),
];
