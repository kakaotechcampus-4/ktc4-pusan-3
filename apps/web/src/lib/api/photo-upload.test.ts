import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { newIdempotencyKey } from "@/lib/api/idempotency";
import { sendPhoto } from "@/lib/api/operations";
import { streamRunEvents } from "@/lib/api/sse";
import { url } from "@/mocks/handlers/helpers";
import { setScenario } from "@/mocks/scenario";
import { server } from "@/mocks/server";

/**
 * 08 사진 — 서버에 S3 버킷이 있으면 사진 본문이 우리 서버를 거치지 않는다.
 *
 * 🚨 S3 는 우리 서버가 아니다. 인증 헤더가 S3 로 새면 안 되고, S3 는 `file` 뒤의 폼 값을 무시해서
 *    `file` 이 맨 마지막이어야 한다. 목 서버(handlers/photos.ts)는 `direct` 만 답해서 여기서 바꿔 끼운다.
 */

const S3_URL = "https://s3.example.test/upload";
const KEY = "photos/p1/k1";

function photo(name = "notice.heic", size = 3): File {
  return new File([new Uint8Array(size)], name, { type: "image/heic" });
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

  it("공지가 아닌 사진으로 실패한 run 의 키는 목도 잊는다 (서버와 같다)", async () => {
    // 화면은 다른 사진을 고르면 키를 돌린다. 목도 실패한 run 의 키를 놓아야 서버와 같게 행동한다
    const key = newIdempotencyKey();
    setScenario("photo_not_document");
    try {
      const first = await sendPhoto("c1", photo("a.heic"), { lane: "document" }, key);
      const seen: string[] = [];
      for await (const event of streamRunEvents(first.run_id)) seen.push(event.type);
      expect(seen.at(-1)).toBe("failed");

      const second = await sendPhoto("c1", photo("b.jpg", 10), { lane: "document" }, key);
      expect(second.run_id).not.toBe(first.run_id);
    } finally {
      setScenario("default");
    }
  });

  it("활동 사진은 S3 에 올리지 않는다 (서버가 아직 받지 않는 아이 사진)", async () => {
    let ticketAsked = false;
    let sentFile = false;
    server.use(
      http.post(url("/children/:cid/photos/uploads"), () => {
        ticketAsked = true;
        return HttpResponse.json(
          { mode: "s3", url: S3_URL, fields: {}, key: KEY },
          { status: 201 },
        );
      }),
      http.post(url("/children/:cid/photos"), async ({ request }) => {
        sentFile = (await request.formData()).get("file") !== null;
        return HttpResponse.json({ run_id: "pr_activity" }, { status: 202 });
      }),
    );

    await sendPhoto("c1", photo(), { lane: "activity" }, newIdempotencyKey());

    expect(ticketAsked).toBe(false);
    expect(sentFile).toBe(true);
  });

  it("같은 사진의 재시도는 S3 에 다시 올리지 않고 처음 열쇠를 보낸다", async () => {
    let s3Posts = 0;
    let photoPosts = 0;
    const keysSent: (FormDataEntryValue | null)[] = [];
    server.use(
      s3Ticket(),
      http.post(S3_URL, () => {
        s3Posts += 1;
        return new HttpResponse(null, { status: 204 });
      }),
      http.post(url("/children/:cid/photos"), async ({ request }) => {
        photoPosts += 1;
        keysSent.push((await request.formData()).get("upload_key"));
        // 처음은 응답이 끊긴다 (서버는 받았을 수도 있다)
        return photoPosts === 1
          ? HttpResponse.error()
          : HttpResponse.json({ run_id: "pr_retry" }, { status: 202 });
      }),
    );
    const key = newIdempotencyKey();

    await expect(sendPhoto("c1", photo(), { lane: "document" }, key)).rejects.toThrow();
    const res = await sendPhoto("c1", photo(), { lane: "document" }, key);

    expect(res.run_id).toBe("pr_retry");
    expect(s3Posts).toBe(1);
    expect(keysSent).toEqual([KEY, KEY]);
  });
});
