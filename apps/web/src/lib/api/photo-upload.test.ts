import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { newIdempotencyKey } from "@/lib/api/idempotency";
import { sendPhoto } from "@/lib/api/operations";
import { url } from "@/mocks/handlers/helpers";
import { server } from "@/mocks/server";

/**
 * 08 사진 — 서버에 S3 버킷이 있으면 사진 본문이 우리 서버를 거치지 않는다.
 *
 * 🚨 S3 는 우리 서버가 아니다. 인증 헤더가 S3 로 새면 안 되고, S3 는 `file` 뒤의 폼 값을 무시해서
 *    `file` 이 맨 마지막이어야 한다. 목 서버(handlers/photos.ts)는 `direct` 만 답해서 여기서 바꿔 끼운다.
 */

const S3_URL = "https://s3.example.test/upload";
const KEY = "photos/p1/k1";

function photo(): File {
  return new File([new Uint8Array([1, 2, 3])], "notice.heic", { type: "image/heic" });
}

function s3Ticket() {
  return http.post(url("/children/:cid/photos/uploads"), () =>
    HttpResponse.json(
      { mode: "s3", url: S3_URL, fields: { key: KEY, policy: "p" }, key: KEY, expires_in: 300 },
      { status: 201 },
    ),
  );
}

describe("08 사진 — S3 로 올리기", () => {
  it("버킷이 있으면 S3 에 먼저 올리고 서버에는 열쇠만 보낸다", async () => {
    let s3Fields: string[] = [];
    let s3Auth: string | null = "unset";
    let photoFields: string[] = [];
    let uploadKey: FormDataEntryValue | null = null;
    server.use(
      s3Ticket(),
      http.post(S3_URL, async ({ request }) => {
        s3Auth = request.headers.get("authorization");
        s3Fields = [...(await request.formData()).keys()];
        return new HttpResponse(null, { status: 204 });
      }),
      http.post(url("/children/:cid/photos"), async ({ request }) => {
        const form = await request.formData();
        photoFields = [...form.keys()];
        uploadKey = form.get("upload_key");
        return HttpResponse.json({ run_id: "pr_s3" }, { status: 202 });
      }),
    );

    const res = await sendPhoto("c1", photo(), { lane: "document" }, newIdempotencyKey());

    expect(res.run_id).toBe("pr_s3");
    expect(s3Fields).toEqual(["key", "policy", "file"]);
    expect(s3Auth).toBeNull();
    expect(photoFields).not.toContain("file");
    expect(uploadKey).toBe(KEY);
  });

  it("S3 에 못 올리면 서버에 읽어 달라고 하지 않는다", async () => {
    let asked = false;
    server.use(
      s3Ticket(),
      http.post(S3_URL, () => new HttpResponse(null, { status: 403 })),
      http.post(url("/children/:cid/photos"), () => {
        asked = true;
        return HttpResponse.json({ run_id: "pr_never" }, { status: 202 });
      }),
    );

    await expect(
      sendPhoto("c1", photo(), { lane: "document" }, newIdempotencyKey()),
    ).rejects.toThrow("사진을 보내지 못했어요");
    expect(asked).toBe(false);
  });

  it("버킷이 없으면 지금처럼 사진을 서버에 보낸다", async () => {
    // 목 서버의 기본 답이 `direct` 다
    const res = await sendPhoto("c1", photo(), { lane: "document" }, newIdempotencyKey());
    expect(res.run_id).toMatch(/^pr_/);
  });
});
