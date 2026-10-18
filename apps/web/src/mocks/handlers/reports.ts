import { http, HttpResponse } from "msw";

import {
  REPORT_CATEGORIES,
  REPORT_MESSAGE_MAX_LENGTH,
  type ReportRequest,
  type ReportResponse,
} from "@/lib/api/types";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";

/**
 * 10 설정 › 문제 알리기 — `POST /reports` (#487 · 서버 PR #488).
 *
 * 서버와 같은 검사를 건다 — 필드는 `category` · `message` 둘뿐(그 밖의 필드는 400), 분류는
 * 네 가지, 본문은 앞뒤 공백을 뗀 1~1000자이고 NUL 문자가 없어야 한다.
 *
 * 🚨 **본문을 들여다보지도 담아 두지도 않는다.** 서버도 DB 에만 두고 로그에 찍지 않는다.
 *    목은 받은 것을 아무 데도 쓰지 않으므로 리셋할 상태도 없다.
 * 🚨 **하루 한도(보호자당 24시간 10건)는 세지 않는다.** 화면이 보는 것은 429 를 받았을 때의
 *    모양이라 `report_limit` 시나리오로 만든다 (04 의 `daily_limit` 과 같은 판단).
 * `report_unreached` 는 첫 요청만 서버에 닿기 전에 끊는다 — 다시 누르면 닿는다 (탈퇴의
 * `withdraw_unreached` 와 같다).
 */

/** `report_unreached` 에서 이미 한 번 끊어 봤는가. 🚨 한 번만 끊는다 — 다시 누르면 닿아야 한다. */
let unreachedOnce = false;

export function resetReportState(): void {
  unreachedOnce = false;
}

const KEYS = ["category", "message"] as const;

function isReportRequest(body: unknown): body is ReportRequest {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return false;
  const record = body as Record<string, unknown>;
  const keys = Object.keys(record);
  if (keys.length !== KEYS.length || !KEYS.every((key) => keys.includes(key))) return false;
  if (!REPORT_CATEGORIES.includes(record.category as ReportRequest["category"])) return false;
  if (typeof record.message !== "string") return false;
  const message = record.message.trim();
  // 서버(Python)는 글자를 코드 포인트로 센다. JS `length` 는 UTF-16 단위라 이모지를 2 로 센다.
  const length = [...message].length;
  return length > 0 && length <= REPORT_MESSAGE_MAX_LENGTH && !message.includes("\u0000");
}

export const reportHandlers = [
  http.post(url("/reports"), async ({ request }) => {
    await networkDelay();
    if (currentScenario() === "report_unreached" && !unreachedOnce) {
      unreachedOnce = true;
      return HttpResponse.error();
    }
    const body: unknown = await request.json().catch(() => null);
    if (!isReportRequest(body)) {
      return apiError(400, "validation_failed", "보낼 내용을 확인해 주세요");
    }
    if (currentScenario() === "report_limit") {
      return apiError(
        429,
        "too_many_reports",
        "지금은 더 보낼 수 없어요. 급한 일이면 이메일로 알려 주세요.",
      );
    }
    const created: ReportResponse = {
      id: crypto.randomUUID(),
      created_at: new Date().toISOString(),
    };
    return HttpResponse.json(created, { status: 201 });
  }),
];
