/**
 * `compare.test.ts` 전용 웹 타입. 비교기가 규칙대로 잡는지를 작은 모양으로 건다 (#559).
 * 화면 코드는 이 파일을 import 하지 않는다.
 */

export type Color = "red" | "blue";

export interface Item {
  id: string;
  note: string | null;
  color: Color;
  label: string;
  extra?: string;
}

export interface Box {
  items: Item[];
  owner: { name: string } | null;
}

export type Reply = { type: "note"; text: string } | { type: "done"; completed: boolean };

export interface SendBody {
  title: string;
  memo?: string;
  starts_at: string | null;
  colors: Color[];
}

/** 갈래마다 칸이 다른 객체 유니온 — 한 갈래에만 있는 칸도 비교에 들어가야 한다. */
export type Mixed = { kind: "a"; only_a: string } | { kind: "b"; only_b: number };
