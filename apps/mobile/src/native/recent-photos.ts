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

import { Platform } from "react-native";

import { File } from "expo-file-system";
import { ImageManipulator, SaveFormat } from "expo-image-manipulator";
/**
 * 🚨 **`expo-media-library` 가 아니라 `/legacy` 다. 이유는 "새 API 가 나쁘다" 가 아니다.**
 *
 * SDK 57 의 새 API(`Query` · `Asset`)는 `ExpoMediaLibraryNext` 네이티브 모듈을 **import 하는
 * 순간** 찾는다. Expo Go 57.0.9 에는 그 모듈이 없어서 `Cannot find native module` 로 **앱이
 * 통째로 안 뜬다** — 사진과 아무 상관 없는 화면을 보려던 사람까지 빈 빨간 화면을 본다.
 *
 * 레거시 쪽은 import 로 죽지 않는다. Expo Go 에서는 권한 호출이 거절될 뿐이고
 * (`Expo Go can no longer provide full access to the media library`), 그건 `ensureReadPermission`
 * 이 `false` 로 받아서 **최근 사진 줄만 안 서는** 상태가 된다 — 계약이 원하는 그 모양 그대로다.
 *
 * 즉 고른 기준은 **실패했을 때 무엇이 남는가** 다. 최근 사진을 실제로 보려면 어느 쪽이든
 * 개발 빌드가 필요하고(§5), 그때 레거시도 똑같이 동작한다.
 * 👉 팀이 Expo Go 를 아예 안 쓰게 되는 날 새 API 로 바꾼다. 하는 일은 같다.
 */
import * as MediaLibrary from "expo-media-library/legacy";

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
 * 방금 내보낸 목록이 어디에 있던 파일인지 기억해 둔다 — `read()` 가 쓸 주소다.
 *
 * 🚨 **`getAssetInfoAsync(id)` 로 다시 묻지 않는다.** 그 함수는 EXIF 를 열어 보기 때문에
 *    Android 에서 `ACCESS_MEDIA_LOCATION` 을 요구하고, 없으면 통째로 거절된다
 *    (`Cannot access ExifInterface because of missing ACCESS_MEDIA_LOCATION permission`).
 *    그 권한은 **사진이 찍힌 장소**를 읽는 권한이라 이 앱이 받을 이유가 없다 (§2 개인정보).
 *    목록을 만들 때 이미 손에 쥐고 있던 값을 기억하면 권한도, 왕복도 필요 없다.
 *
 * 🚨 **id 를 파일 경로로 바꾸지 않는다.** 웹에 주는 `id` 는 끝까지 불투명한 값이어야 한다 —
 *    경로를 넘기면 사진이 어디 있는지가 화면 코드와 로그로 새어 나간다.
 *
 * `list()` 를 부를 때마다 통째로 갈아 끼운다. 웹이 다시 물어본 뒤에 고르기 때문에
 * 이 표는 화면에 보이는 줄보다 항상 새것이고, 무한정 쌓이지도 않는다.
 */
let lastListed = new Map<string, { uri: string; filename: string }>();

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
  try {
    const current = await MediaLibrary.getPermissionsAsync(false, ["photo"]);
    // `limited`(Android 14+ · iOS 14+ 의 "선택한 사진만") 도 읽을 수 있는 상태다.
    // 고른 몇 장만 보이는 것이 정상이고, 더 고르라고 시트를 띄우는 것은 셸이 할 일이 아니다.
    if (current.granted || current.accessPrivileges === "limited") return true;
    if (!current.canAskAgain) return false;

    const asked = await MediaLibrary.requestPermissionsAsync(false, ["photo"]);
    return asked.granted || asked.accessPrivileges === "limited";
  } catch {
    // 🚨 **권한 API 자체가 거절될 수 있다.** Expo Go 가 그렇다 — 묻기도 전에 예외가 온다.
    //    그건 "권한이 없다" 와 같은 뜻이고, 여기서 삼켜야 호출부가 예외를 몰라도 된다.
    return false;
  }
}

/**
 * 최근 사진 `limit` 장. 못 읽으면 빈 배열이다.
 *
 * 🚨 **한 장이 실패해도 나머지는 돌려준다.** 손상된 파일 하나가 줄 전체를 지우지 않도록
 *    썸네일을 못 만든 장만 건너뛴다.
 */
export async function listRecentPhotos(limit: number): Promise<NativeRecentPhoto[]> {
  if (!(await ensureReadPermission())) return [];

  const page = await MediaLibrary.getAssetsAsync({
    first: limit,
    mediaType: MediaLibrary.MediaType.photo,
    // 🚨 최신순이다. 방금 찍은 알림장이 맨 앞에 와야 이 줄이 "앨범에서 고르기" 보다 빠르다.
    sortBy: [[MediaLibrary.SortBy.creationTime, false]],
  });

  const listed = new Map<string, { uri: string; filename: string }>();
  const photos: NativeRecentPhoto[] = [];
  for (const asset of page.assets) {
    const thumbnailUrl = await makeThumbnail(asset.uri);
    if (!thumbnailUrl) continue;
    listed.set(asset.id, { uri: asset.uri, filename: asset.filename });
    photos.push({ id: asset.id, thumbnailUrl });
  }

  lastListed = listed;
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
  // 🚨 목록에 없던 id 는 읽지 않는다. 우리가 내보낸 적 없는 파일을 열어 주는 창구가 되면 안 된다.
  const listed = lastListed.get(id);
  if (!listed) return null;
  if (!(await ensureReadPermission())) return null;

  try {
    const uri = await readableUriFor(id, listed.uri);
    if (!uri) return null;
    const file = new File(uri);
    if (!file.exists || file.size <= 0 || file.size > MAX_ORIGINAL_BYTES) return null;

    return {
      base64: await file.base64(),
      // `file.type` 이 비면 확장자로 넘겨짚는다. 웹은 이 값을 그대로 업로드에 싣는다.
      mimeType: file.type || guessMimeType(listed.filename),
      filename: listed.filename,
    };
  } catch {
    return null;
  }
}

/**
 * 실제로 **파일로 열 수 있는** 주소.
 *
 * - Android 는 목록이 준 `file://` 이 그대로 파일이다.
 * - 🚨 **iOS 는 아니다.** 목록이 주는 것은 `ph://`(사진 보관함 식별자)라 `File` 로 못 연다.
 *   `getAssetInfoAsync` 가 복사해 준 `localUri` 가 필요하고, 그건 iOS 에서는 권한을 더 묻지
 *   않는다 — 위에서 피한 `ACCESS_MEDIA_LOCATION` 은 Android 쪽 문제다.
 */
async function readableUriFor(id: string, listedUri: string): Promise<string | null> {
  if (Platform.OS !== "ios") return listedUri;
  const info = await MediaLibrary.getAssetInfoAsync(id);
  return info.localUri ?? null;
}

/**
 * 원본을 `data:` 썸네일로 줄인다. 실패하면 `null` — 그 한 장만 줄에서 빠진다.
 *
 * 🚨 **`saveAsync()` 는 캐시에 파일을 하나 남긴다.** base64 를 손에 넣은 뒤에는 쓸 데가 없고,
 *    지우지 않으면 시트를 열 때마다 **사용자 사진의 복사본이 앱 캐시에 쌓인다** (§2 개인정보).
 */
async function makeThumbnail(uri: string): Promise<string | null> {
  try {
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
