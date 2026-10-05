/**
 * 조건부 클래스 합치기. clsx 를 넣지 않은 이유는 하는 일이 이게 전부라서다.
 *
 * ⚠️ tailwind-merge 가 아니다 — 뒤에 온 클래스가 앞을 덮어주지 않는다.
 *    primitive 안에서 변형(variant)을 통째로 골라 쓰고, 바깥에서 색·크기를 덮어쓰지 않는다.
 */
export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}
