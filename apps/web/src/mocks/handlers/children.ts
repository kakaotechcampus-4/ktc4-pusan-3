import { http, HttpResponse } from "msw";

import type { HealthSafety, OnboardingRequest } from "@/lib/api/types";
import { isSeverityFor } from "@/lib/health-safety";
import { isMeasurementInRange } from "@/lib/measurement";

import {
  CHILD_ID,
  daysAgo,
  emptyHome,
  healthSafety,
  home,
  me,
  newHealthSafety,
  safetyScan,
  staleAffinities,
} from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, consentRequired, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";
import { joinChild } from "./membership";
import { invalidPolicyVersion } from "./policies";
import { saveOnboarding } from "./profile";
import { recordConsent } from "./settings";

/** 02 온보딩 본문이 받는 필드 — 서버 `OnboardingRequest`(extra="forbid")와 같다. */
const ONBOARDING_FIELDS = new Set(["relation", "gender", "height_cm", "weight_kg"]);

/**
 * 등록된 안전 정보. 🚨 **예전에는 Set 하나였다** — 등록 여부만 알면 409 를 낼 수 있어서였다.
 * 11 아이 프로필이 목록을 읽고 회수까지 하면서, `GET` 이 방금 등록한 항목을 돌려주지 않으면
 * 화면이 "저장됐다는데 목록에 없다" 로 보인다. 그래서 목이 **실제 목록을 들고 있는다.**
 *
 * 🚨 회수는 행을 지우지 않는다 — 계약서는 `state = 'retracted'` 로의 전환이라고 정했다.
 *    목록·사용에서 빠지는 것은 `active` 필터가 하고, 행은 남는다 (증빙).
 */
interface SafetyRow {
  safety: HealthSafety;
  retracted: boolean;
}

let safetyState: SafetyRow[] = healthSafety.map((safety) => ({ safety, retracted: false }));

function activeSafety(): HealthSafety[] {
  return safetyState.filter((row) => !row.retracted).map((row) => row.safety);
}

/**
 * 알레르기 항목 하나의 지금 상태 — 제안 목록의 사전검사(`handlers/suggestions.ts`)가 읽는다.
 * `null` 은 행이 없다는 뜻이다 (서버의 `unknown` · 저장하지 않는 상태).
 * ⚠️ 서버의 `none` 은 아직 목 모양(`HealthSafety`)에 없다 — 옛 모양이라 #87 때 같이 맞춘다.
 * ⚠️ **label 이 항목 이름과 글자로 같을 때만 짝짓는다** — 픽스처가 정식 이름만 쓰는 전제다.
 *    실제 label 은 보호자의 자유 입력("우유 알레르기" · "Milk" · "우유, 땅콩")이라 서버는
 *    문자열 비교가 아니라 알레르기 사전으로 짝짓는다 (#264). 이 함수를 서버 구현의 본으로 삼지 말 것.
 */
export function allergyStatus(label: string): "active" | "retracted" | null {
  const rows = safetyState.filter(
    (row) => row.safety.type === "allergy" && row.safety.label === label,
  );
  if (rows.some((row) => !row.retracted)) return "active";
  return rows.length > 0 ? "retracted" : null;
}

/** 테스트용. 목 서버는 프로세스 수명만큼 살아 있다. */
export function resetSafetyState(): void {
  safetyState = healthSafety.map((safety) => ({ safety, retracted: false }));
}

/** 01·02 첫 진입 · 온보딩, 03 홈. */
export const childrenHandlers = [
  /**
   * 🚨 **아이와 아이 동의가 한 트랜잭션이다** (#96 · ⚠️ 계약 확정 전).
   *    동의를 아이 단위로 기록하면 `POST /consents` 로는 저장할 수 없다 — 그 엔드포인트는
   *    `child_id` 를 받는데 이 호출 전에는 그 id 가 없다. 계약서 §04 는 `child_basic` 없이
   *    이 호출이 403 이라고 말하므로, 둘을 동시에 만족시키는 모양은 이것뿐이다.
   *
   * 🚨 목이라고 **아무거나 201 로 돌려주지 않는다.** 동의가 빠지면 막는다 — 화면이 체크값을
   *    실제로 실어 보내는지, 법정대리인 확인을 상수 true 로 굳혀 두지 않았는지를 여기서 건다.
   */
  http.post(url("/children"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as {
      nickname: string;
      consents?: Array<{ scope: string; policy_version?: string }>;
      guardian_attested?: boolean;
    };

    const scopes = (body.consents ?? []).map((c) => c.scope);
    const missing = ["child_basic", "child_health"].filter((s) => !scopes.includes(s));
    if (missing.length > 0) {
      return apiError(403, "consent_required", "아이 정보에 대한 동의가 필요해요", {
        scopes: missing,
      });
    }
    // 법정대리인 확인은 그 동의의 **유효 요건**이다 (개인정보보호법 제22조의2) —
    // 없으면 동의가 있어도 저장하지 않는다.
    if (body.guardian_attested !== true) {
      return apiError(403, "consent_required", "법정대리인 확인이 필요해요", {
        scopes: ["child_basic"],
      });
    }

    // 🚨 **등록된 약관 버전이어야 한다** (실서버 · #91). 아이가 만들어지기 전에 막는다 —
    //    아이는 남고 동의만 어긋나는 상태를 만들지 않는다.
    const invalid = invalidPolicyVersion(body.consents ?? []);
    if (invalid) {
      return apiError(400, "policy_version_invalid", "동의 화면을 다시 불러와 주세요", invalid);
    }

    // 🚨 관계를 여기서 정하지 않는다 — 01 은 더 이상 보내지 않고 02 가 받는다.
    //    그때까지는 비어 있는 것이 사실이라, 목이 "엄마" 로 채워 두지 않는다.
    joinChild({
      child_id: CHILD_ID,
      nickname: body.nickname,
      age_display: "만 4세",
      relation: "other",
      role: "owner",
      consent_required: [],
    });
    // 🚨 **아이 동의도 같은 표에 쌓는다** (`handlers/settings.ts`). 실서버도 아이 등록에서
    //    동의 행을 남기고, 10 설정의 `GET /consents?child_id=` 가 그 행을 돌려준다.
    for (const consent of body.consents ?? []) {
      recordConsent(CHILD_ID, consent.scope, "granted", consent.policy_version ?? "unknown");
    }
    return HttpResponse.json(
      { id: CHILD_ID, nickname: body.nickname, age_display: "만 4세", role: "owner" },
      { status: 201 },
    );
  }),

  // 발달 검사가 아니다 — 보호자가 고른 값만 저장하고 AI 는 평가하지 않는다.
  http.get(url("/dev-screening/items"), async () => {
    await networkDelay();
    return HttpResponse.json({
      age_band: "만 4세 · 유아",
      items: [
        {
          item_id: "d4a",
          text: "가위로 선을 따라 종이를 자른다.",
          levels: [
            { level: 1, label: "아직" },
            { level: 2, label: "가끔" },
            { level: 3, label: "자주" },
          ],
        },
        {
          item_id: "d4b",
          text: "친구와 번갈아 가며 논다.",
          levels: [
            { level: 1, label: "아직" },
            { level: 2, label: "가끔" },
            { level: 3, label: "자주" },
          ],
        },
      ],
    });
  }),

  /**
   * 02 아이 정보. 서버 구현(PR #353)을 따른다 — 응답은 `{child, growth}` 고, 11 프로필과
   * 같은 상태를 고친다 (`handlers/profile.ts` 의 `saveOnboarding`).
   *
   * 🚨 **바디 오류는 400 이다.** 전부 선택이지만 빈 본문 · `null` · 범위 밖 키와 몸무게 ·
   *    모르는 필드(`safety` · `one_line` 등)는 받지 않는다. 화면은 아무것도 안 적으면 보내지 않는다.
   *
   * ⚠️ **본문이 계약서 §05 와 달라졌다** (확정 전) — `relation` · `gender` · `height_cm` ·
   *    `weight_kg` 가 들어오고 `interests` · `dev_answers` 가 빠졌다 (`OnboardingRequest`).
   *
   * ⚠️ **알레르기가 이 본문에서 빠졌다.** 02 가 11 과 같은 구역을 쓰면서 등록이 승인 게이트
   *    ㉡ 로만 간다 (`components/safety-section.tsx`). `safety_status`(없음/잘 모르겠어요)를
   *    물을 자리가 지금 없는 것은 **열린 결정**이다 (`OnboardingRequest` 주석).
   */
  http.post(
    url("/children/:cid/onboarding"),
    withIdempotency(async ({ request }) => {
      await networkDelay(400);
      if (currentScenario() === "consent") return consentRequired("child_health");

      const body = (await request.json()) as Record<string, unknown>;

      // 🚨 알레르기는 이 경로로 오지 않는다 — 승인 게이트 ㉡ 가 유일한 쓰기 경로다 (서버 extra="forbid").
      const unknown = Object.keys(body).filter((name) => !ONBOARDING_FIELDS.has(name));
      const bad = Object.entries(body)
        .filter(([name, value]) => {
          if (!ONBOARDING_FIELDS.has(name)) return false;
          if (value === null) return true;
          return (
            (name === "height_cm" || name === "weight_kg") &&
            (typeof value !== "number" ||
              !isMeasurementInRange(name === "height_cm" ? "height" : "weight", value))
          );
        })
        .map(([name]) => name);
      if (unknown.length > 0 || bad.length > 0) {
        return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
          fields: [...unknown, ...bad],
        });
      }
      if (Object.keys(body).length === 0) {
        return apiError(400, "validation_failed", "저장할 내용이 없어요", { fields: [] });
      }
      const input = body as Pick<
        OnboardingRequest,
        "relation" | "gender" | "height_cm" | "weight_kg"
      >;

      // 관계는 `parent_child` 행의 값이다 — 보냈으면 `GET /me` 에도 그 값으로 서야 한다.
      if (input.relation) {
        joinChild({
          child_id: CHILD_ID,
          nickname: me.children[0]?.nickname ?? "민준",
          age_display: "만 4세",
          relation: input.relation,
          role: "owner",
          consent_required: [],
        });
      }

      return HttpResponse.json(saveOnboarding(input));
    }),
  ),

  /**
   * 🚨 승인 게이트 ㉡ — 알레르기·건강 기록의 유일한 쓰기 경로 (NF-03).
   *
   * 확정 게이트와 같은 구조다. 같은 키 재시도는 withIdempotency 가 처음 응답을 재생하고,
   * 여기 409 는 **새 요청으로 같은 항목을 또 넣으려는 경우** 에만 나온다
   * (계약서: UNIQUE(child_id, type, label) — 같은 항목 재등록은 409).
   */
  http.post(
    url("/children/:cid/health-safety"),
    withIdempotency(async ({ request }) => {
      await networkDelay();
      if (currentScenario() === "consent") return consentRequired("child_health");

      const body = (await request.json()) as {
        type: string;
        label: string;
        severity?: string | null;
        reactions?: string[];
        notes?: string | null;
      };
      // 🚨 DB CHECK `health_safety_severity_by_kind` 를 목이 대신 막는다 — 알레르기에 가볍게~심하게
      //    를 보내면 실서버에서는 등록이 통째로 실패한다. 목이 받아 주면 화면이 그걸 모르고 지나간다.
      if (body.severity != null && !isSeverityFor(body.type, body.severity)) {
        return apiError(400, "validation_failed", "이 종류에는 쓸 수 없는 심각도예요", {
          fields: ["severity"],
        });
      }
      // 🚨 서버는 이름의 앞뒤 공백을 떼고 저장하고, 같은 항목인지도 뗀 이름으로 본다 (#369).
      //    목이 원문으로 비교하면 " 땅콩" 이 목에서는 새 항목, 실서버에서는 409 가 된다.
      const label = body.label.trim();
      const exists = safetyState.some(
        (row) => !row.retracted && row.safety.type === body.type && row.safety.label === label,
      );
      if (exists) {
        return apiError(409, "already_exists", "이미 등록된 항목이에요");
      }

      // 메모도 앞뒤 공백을 떼고 빈 글자는 null 로 저장한다 (서버 #370) — 고치기의 "바뀐 것 없음" 판정과 맞춘다.
      const safety = newHealthSafety({ ...body, label, notes: body.notes?.trim() || null });
      safetyState = [{ safety, retracted: false }, ...safetyState];

      return HttpResponse.json({ safety }, { status: 201 });
    }),
  ),

  // 🚨 `POST /children/{cid}/photos` 는 여기 없다 — 08 화면이 생기면서
  //    `handlers/photos.ts` 로 옮겼다 (run 등록부를 그 파일이 들고 있어야 SSE 가 갈린다).

  http.get(url("/children/:cid/home"), async () => {
    await networkDelay();
    const scenario = currentScenario();
    if (scenario === "consent") return consentRequired("child_health");
    if (scenario === "empty") return HttpResponse.json(emptyHome);
    if (scenario === "stale") {
      // 기억은 있는데 전부 낡았다. highlight 는 뜨지만 근거로는 못 쓴다.
      return HttpResponse.json({
        ...home,
        week_count: 0,
        highlight: {
          text: "계란 반찬을 찾은 기록이 있어요.",
          state_reason: staleAffinities[0].state_reason,
          ref: { kind: "profile_affinity" as const, id: staleAffinities[0].id },
        },
      });
    }
    return HttpResponse.json(home);
  }),

  // 07 기억 화면이 쓰는 `/observations` · `/affinities` 는 `handlers/memories.ts` 가 갖는다 —
  // 예전에 여기 있던 자리표시 응답은 계약서 모양(`{ affinities, safety }`)과 달랐다.

  http.get(url("/children/:cid/health-safety"), async () => {
    await networkDelay();
    if (currentScenario() === "empty")
      return HttpResponse.json({ items: [], updated_at: daysAgo(0) });
    return HttpResponse.json({ items: activeSafety(), updated_at: daysAgo(3) });
  }),

  /**
   * 11 알레르기 검사지 읽기. ⚠️ **계약서 v1 에 없다** (이슈 #86).
   *
   * 🚨 **저장하지 않는다.** 검사지에 적힌 것을 옮겨 오기만 하고, 저장은 보호자가 승인한 뒤
   *    바로 위 `POST /health-safety`(승인 게이트 ㉡) 가 한다 — 쓰기 경로는 여전히 하나다.
   *    그래서 `withIdempotency` 로 감싸지 않는다 (되돌릴 수 없는 5개에 이 경로가 없다).
   *
   * 🚨 **못 읽은 칸을 채워 보내지 않는다.** `null` 로 내리고 화면이 그 자리를 비운다 —
   *    LLM 이 건강 정보를 추론하지 않는다는 규칙이 여기서 지켜지거나 깨진다 (최상위 §2).
   */
  http.post(url("/children/:cid/health-safety/scan"), async () => {
    // 사진을 읽는 시간. 스피너와 "읽고 있어요" 가 실제로 보이려면 짧으면 안 된다.
    await networkDelay(1400);
    if (currentScenario() === "consent") return consentRequired("child_health");
    return HttpResponse.json(safetyScan);
  }),

  /**
   * 🚨 승인 게이트 ㉡ — **고치기.** 서버 #370 (`PATCH /children/{cid}/health-safety/{id}`).
   *
   * 🚨 **제자리에서 바꾸지 않는다.** 지금 기록을 내리고 바뀐 값으로 **새 id** 의 기록을 넣는다 —
   *    이력이 남는다 (#87 결정). 바뀐 것이 없으면 새 기록 없이 지금 기록을 그대로 돌려준다.
   * 🚨 `type` · `label` 은 받지 않는다(400). 그 둘은 이 기록의 정체다.
   * 🚨 `severity: null` 은 "모르겠어요 로 되돌리기", `notes: null` 은 비우기, 안 보낸 칸은 그대로다.
   *    증상은 보내면 통째로 바뀌고 `null` 은 400 이다.
   * 🚨 키는 서버도 필수다(없으면 400 `idempotency_key_required`). 화면은 늘 붙이고(`idempotentPath`),
   *    목도 키 없는 요청을 막는다 — 화면이 키를 빼먹으면 여기서 드러나게.
   */
  http.patch(
    url("/children/:cid/health-safety/:id"),
    withIdempotency(async ({ request, params }) => {
      await networkDelay(320);

      // 서버 순서: 본문 형식(400) → 키(400) → 동의(403) → 없는 기록(404) → 심각도(400).
      const body = (await request.json()) as Record<string, unknown>;
      if ("type" in body || "label" in body) {
        return apiError(400, "validation_failed", "종류와 이름은 고칠 수 없어요");
      }
      if ("reactions" in body && !Array.isArray(body.reactions)) {
        return apiError(400, "validation_failed", "증상을 비우려면 빈 목록을 보내 주세요");
      }
      if (currentScenario() === "consent") return consentRequired("child_health");

      const row = safetyState.find((item) => item.safety.id === params.id);
      if (!row || row.retracted) return apiError(404, "not_found", "이미 내려간 기록이에요");
      const current = row.safety;

      const severity =
        "severity" in body
          ? body.severity === null
            ? null
            : String(body.severity)
          : current.severity;
      if (severity !== null && !isSeverityFor(current.type, severity)) {
        return apiError(400, "validation_failed", "이 종류에는 쓸 수 없는 심각도예요", {
          fields: ["severity"],
        });
      }
      const reactions = Array.isArray(body.reactions)
        ? body.reactions.map((r) => String(r).trim())
        : current.reactions;
      // 서버처럼 메모의 앞뒤 공백을 떼고 빈 글자는 null 로 — "" 와 null 이 다르면 안 고친 고치기가 새 기록을 만든다.
      const notes =
        ("notes" in body
          ? body.notes === null
            ? null
            : String(body.notes)
          : current.notes
        )?.trim() || null;

      const unchanged =
        severity === current.severity &&
        JSON.stringify(reactions) === JSON.stringify(current.reactions) &&
        notes === (current.notes?.trim() || null);
      if (unchanged) return HttpResponse.json({ safety: current });

      row.retracted = true;
      const safety = {
        ...newHealthSafety({
          type: current.type,
          label: current.label,
          severity,
          reactions,
          notes,
        }),
        // 서버는 관리 정보를 새 기록으로 넘겨 준다 — 이 API 로는 받지 않는 칸이다.
        management: current.management,
      };
      safetyState = [{ safety, retracted: false }, ...safetyState];
      return HttpResponse.json({ safety });
    }),
  ),

  /**
   * 🚨 회수 — `state = 'retracted'` 로의 전환이다 (계약서 §10). **행을 지우지 않는다.**
   *    잘못 등록했거나 더 이상 적용하지 않는 항목을 보호자가 내린다.
   *
   * 🚨 `Idempotency-Key` 를 요구하지 않는다. 같은 회수를 두 번 보내도 결과가 같다 (아래 404 는
   *    **이미 내려간 id** 를 가리킬 때다). 고치기(PATCH)는 새 기록을 넣어서 키를 단다.
   */
  http.delete(url("/children/:cid/health-safety/:id"), async ({ params }) => {
    await networkDelay();
    const row = safetyState.find((item) => item.safety.id === params.id);
    if (!row || row.retracted) {
      return apiError(404, "not_found", "이미 내려간 기록이에요");
    }
    row.retracted = true;
    return new HttpResponse(null, { status: 204 });
  }),
];
