/**
 * 최근 사진 — **웹이 못 하는 절반.**
 *
 * 웹 쪽 절반은 `apps/web/src/lib/native/recent-photos.ts` 에 이미 있다. 거기 적힌 대로
 * 브라우저는 기기 갤러리 **목록**을 읽을 수 없고(`<input type="file">` 은 OS 피커를 열 뿐이다),
 * 그래서 08 사진 시트의 "최근 사진" 줄은 셸이 값을 꽂아 줘야만 선다. 이 파일이 그 값을 만든다.
 *
 * 🚨 **화면을 만들지 않는다** (CLAUDE.md §1). 여기는 사진을 읽어 **모양만 맞춰 돌려주고** 끝이고,
 *    줄을 그릴지 말지 · 권한이 없을 때 무엇을 보여줄지는 전부 웹이 정한다.
 *
 * 🚨 **실패는 예외가 아니라 빈 값이다.** 권한이 없든 파일이 깨졌든 `list()` 는 `[]`,
 *    `read()` 는 `null` 이다 — 웹 계약이 그렇게 적혀 있고, 그래야 촬영 · 앨범이라는 길이
 *    그대로 남는다. 사진을 못 읽는 것은 이 흐름의 실패가 아니다.
 *
 * 🚨 **사진 경로 · 파일 이름 · 썸네일을 로그에 찍지 않는다** (최상위 CLAUDE.md §2 개인정보).
 *    이 파일에 `console.*` 이 없는 것은 실수가 아니다.
 */

import { File } from "expo-file-system";
import { ImageManipulator, SaveFormat } from "expo-image-manipulator";
import { Asset, AssetField, MediaType, Query } from "expo-media-library";
import * as MediaLibrary from "expo-media-library";

/**
 * 썸네일 가로 폭. 웹의 칸이 `size-20`(80px)이라 3배수 화면까지 덮는 값으로 잡는다.
 * 🚨 여기서 키우면 `list()` 한 번에 실려 가는 base64 가 그대로 커진다 — 12장이 한 번에 간다.
 */
const THUMBNAIL_WIDTH = 240;

/** 썸네일은 보기용이라 화질을 아낀다. 분석에 쓰는 것은 `read()` 가 주는 원본이다. */
const THUMBNAIL_QUALITY = 0.6;

/**
 * 이보다 큰 사진은 읽지 않고 `null` 로 답한다.
 * 🚨 원본 바이트는 base64 로 부풀어(≈4/3배) 웹뷰 경계를 넘어간다. 한도 없이 넘기면
 *    큰 사진 한 장에 앱이 멈춘 것처럼 보인다 — 그때는 "앨범에서 고르기" 가 더 빠른 길이고,
 *    웹은 `null` 을 받으면 정확히 그렇게 안내한다.
 */
const MAX_ORIGINAL_BYTES = 20 * 1024 * 1024;

/** 웹 계약의 `RecentPhoto` 와 같은 모양. 셸이 만들어 넘기는 쪽이다. */
export interface NativeRecentPhoto {
  id: string;
  /** 🚨 `data:` 여야 한다. 웹뷰 안의 페이지는 `file:` 을 열 수 없다. */
  thumbnailUrl: string;
}

/** 원본 한 장. 웹 쪽 브릿지가 이 값으로 `File` 을 만든다. */
export interface NativeOriginalPhoto {
  base64: string;
  mimeType: string;
  filename: string;
}

/**
 * 사진 읽기 권한을 확보한다.
 *
 * 🚨 **`granularPermissions: ["photo"]` 로 사진만 묻는다.** 기본값은 사진 · 영상 · 오디오를
 *    전부 묻는데, 이 앱이 필요한 것은 사진뿐이다 — 필요 없는 권한을 묻지 않는 것이
 *    "필수 질문을 늘리지 말 것"(최상위 CLAUDE.md §2 개인정보) 과 같은 규칙이다.
 *
 * 🚨 **거부를 설명하지 않는다.** 여기서 안내 화면을 띄우면 화면이 두 벌이 된다 (§1).
 *    거부하면 그냥 `false` 고, 웹은 줄을 안 그린다.
 */
async function ensureReadPermission(): Promise<boolean> {
  const current = await MediaLibrary.getPermissionsAsync(false, ["photo"]);
  // `limited`(Android 14+ · iOS 14+ 의 "선택한 사진만") 도 읽을 수 있는 상태다.
  // 고른 몇 장만 보이는 것이 정상이고, 더 고르라고 시트를 띄우는 것은 셸이 할 일이 아니다.
  if (current.granted || current.accessPrivileges === "limited") return true;
  if (!current.canAskAgain) return false;

  const asked = await MediaLibrary.requestPermissionsAsync(false, ["photo"]);
  return asked.granted || asked.accessPrivileges === "limited";
}

/**
 * 최근 사진 `limit` 장. 못 읽으면 빈 배열이다.
 *
 * 🚨 **한 장이 실패해도 나머지는 돌려준다.** 손상된 파일 하나가 줄 전체를 지우지 않도록
 *    썸네일을 못 만든 장만 건너뛴다.
 */
export async function listRecentPhotos(limit: number): Promise<NativeRecentPhoto[]> {
  if (!(await ensureReadPermission())) return [];

  const assets = await new Query()
    .eq(AssetField.MEDIA_TYPE, MediaType.IMAGE)
    .orderBy({ key: AssetField.CREATION_TIME, ascending: false })
    .limit(limit)
    .exe();

  const photos: NativeRecentPhoto[] = [];
  for (const asset of assets) {
    const thumbnailUrl = await makeThumbnail(asset);
    if (thumbnailUrl) photos.push({ id: asset.id, thumbnailUrl });
  }
  return photos;
}

/**
 * 고른 한 장의 원본. 못 읽으면 `null`.
 *
 * 🚨 **줄이거나 다시 압축하지 않는다.** 이 사진은 알림장 글자를 읽는 데 쓰인다 — 08 의
 *    "앨범에서 고르기" 가 넘기는 것도 원본이라, 여기만 줄이면 **같은 사진인데 경로에 따라
 *    분석 결과가 달라진다.**
 */
export async function readRecentPhoto(id: string): Promise<NativeOriginalPhoto | null> {
  if (!(await ensureReadPermission())) return null;

  try {
    const info = await new Asset(id).getInfo();
    const file = new File(info.uri);
    if (!file.exists || file.size <= 0 || file.size > MAX_ORIGINAL_BYTES) return null;

    return {
      base64: await file.base64(),
      // `file.type` 이 비면 확장자로 넘겨짚는다. 웹은 이 값을 그대로 업로드에 싣는다.
      mimeType: file.type || guessMimeType(info.filename),
      filename: info.filename,
    };
  } catch {
    return null;
  }
}

/**
 * 원본을 `data:` 썸네일로 줄인다. 실패하면 `null` — 그 한 장만 줄에서 빠진다.
 *
 * 🚨 **`saveAsync()` 는 캐시에 파일을 하나 남긴다.** base64 를 손에 넣은 뒤에는 쓸 데가 없고,
 *    지우지 않으면 시트를 열 때마다 **사용자 사진의 복사본이 앱 캐시에 쌓인다** (§2 개인정보).
 */
async function makeThumbnail(asset: Asset): Promise<string | null> {
  try {
    const uri = await asset.getUri();
    const image = await ImageManipulator.manipulate(uri)
      .resize({ width: THUMBNAIL_WIDTH })
      .renderAsync();
    const saved = await image.saveAsync({
      format: SaveFormat.JPEG,
      compress: THUMBNAIL_QUALITY,
      base64: true,
    });

    deleteQuietly(saved.uri);
    return saved.base64 ? `data:image/jpeg;base64,${saved.base64}` : null;
  } catch {
    return null;
  }
}

/** 캐시 청소는 실패해도 흐름을 막지 않는다 — 다음 번 OS 청소가 가져간다. */
function deleteQuietly(uri: string): void {
  try {
    new File(uri).delete();
  } catch {
    // 지우지 못한 것은 화면에 영향이 없다.
  }
}

/** `file.type` 이 빈 문자열일 때만 쓰는 최후의 수단. */
function guessMimeType(filename: string): string {
  const extension = filename.toLowerCase().split(".").pop() ?? "";
  switch (extension) {
    case "png":
      return "image/png";
    case "webp":
      return "image/webp";
    case "gif":
      return "image/gif";
    case "heic":
      return "image/heic";
    case "heif":
      return "image/heif";
    default:
      return "image/jpeg";
  }
}
