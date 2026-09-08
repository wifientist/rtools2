import { dagreEngine } from "./dagre";
import { forceEngine } from "./force";
import { geoEngine } from "./geo";
import { gridEngine } from "./grid";
import { manualEngine } from "./manual";
import type { LayoutEngine } from "./types";

/** Every layout engine, in the order the picker offers them. */
export const ENGINES: LayoutEngine[] = [
  dagreEngine,
  forceEngine,
  gridEngine,
  geoEngine,
  manualEngine,
];

export const ENGINE_BY_ID: Record<string, LayoutEngine> = Object.fromEntries(
  ENGINES.map((e) => [e.id, e]),
);

export function engineOr(id: string): LayoutEngine {
  return ENGINE_BY_ID[id] ?? dagreEngine;
}
