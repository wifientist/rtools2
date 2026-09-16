/**
 * Where each port chip sits, laid out the way a RUCKUS ICX faceplate is.
 *
 *   1/1/1  1/1/3  1/1/5 …   1/2/1  1/2/3
 *   1/1/2  1/1/4  1/1/6 …   1/2/2  1/2/4
 *
 * Odd ports on the top row, even ports beneath them, running left to right.
 * Each module (1/1/x, 1/2/x, 1/3/x) is its own block, separated from the next
 * by a slightly wider gap than the one between ports. Each stack unit (1/x/x,
 * 2/x/x, …) is a separate physical switch, so it gets its own two-row band
 * beneath the one before.
 *
 * Position comes from the port NUMBER, never from where the port sits in the
 * list. That matters twice over: R1 serves ports in lexicographic order
 * (1/1/1, 1/1/10, 1/1/11, 1/1/12, 1/1/2 …), and a port R1 happens to omit must
 * leave a hole rather than shift everything after it one place to the left —
 * otherwise the strip stops matching the switch you are standing in front of.
 */

export const CHIP = 13;
/** Between neighbouring ports. */
export const GAP = 2;
/** Between modules: wider than GAP so the blocks read as separate, still small. */
export const MODULE_GAP = 6;
/** Between stack units. */
export const UNIT_GAP = 6;
/** Left gutter holding the unit number. Only present on a stack. */
export const UNIT_GUTTER = 12;

const PITCH = CHIP + GAP;
const IDENT = /^(\d+)\/(\d+)\/(\d+)$/;

export type Cell = { x: number; y: number };

export type PanelLayout = {
  cells: Record<string, Cell>;
  /** One per stack unit, for labelling. Empty on a standalone switch. */
  units: { unit: string; y: number }[];
  width: number;
  height: number;
};

type Parsed = { unit: number; module: number; port: number };

/** Natural sort for idents that are not unit/module/port, so "lag10" follows "lag9". */
function natural(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true });
}

export function panelLayout(idents: string[]): PanelLayout {
  const cells: Record<string, Cell> = {};
  // unit -> module -> port -> ident
  const tree = new Map<number, Map<number, Map<number, string>>>();
  const other: string[] = [];

  for (const ident of new Set(idents)) {
    const match = IDENT.exec(ident);
    if (!match) {
      other.push(ident);
      continue;
    }
    const [unit, module, port]: number[] = match.slice(1).map(Number);
    const parsed: Parsed = { unit, module, port };
    if (!tree.has(parsed.unit)) tree.set(parsed.unit, new Map());
    const modules = tree.get(parsed.unit)!;
    if (!modules.has(parsed.module)) modules.set(parsed.module, new Map());
    modules.get(parsed.module)!.set(parsed.port, ident);
  }

  const stacked = tree.size > 1;
  const left = stacked ? UNIT_GUTTER : 0;
  const units: PanelLayout["units"] = [];
  let y = 0;
  let width = 0;

  const bandHeight = (rows: number) => rows * PITCH - GAP;

  for (const unit of [...tree.keys()].sort((a, b) => a - b)) {
    const modules = tree.get(unit)!;
    let x = left;
    let rows = 1;
    for (const module of [...modules.keys()].sort((a, b) => a - b)) {
      const ports = modules.get(module)!;
      let columns = 0;
      for (const [port, ident] of ports) {
        // Port 1 -> column 0 row 0, port 2 -> column 0 row 1, port 3 -> column 1 row 0.
        const column = Math.floor((port - 1) / 2);
        const row = (port - 1) % 2;
        rows = Math.max(rows, row + 1);
        columns = Math.max(columns, column + 1);
        cells[ident] = { x: x + column * PITCH, y: y + row * PITCH };
      }
      // The module's own width, then the separator in place of the usual gap.
      x += columns * PITCH - GAP + MODULE_GAP;
    }
    width = Math.max(width, x - MODULE_GAP);
    if (stacked) units.push({ unit: String(unit), y });
    y += bandHeight(Math.max(rows, 2)) + UNIT_GAP;
  }

  // Anything that is not unit/module/port — a management port, a name R1
  // invents — still gets drawn, on its own row after the faceplate, rather
  // than silently vanishing from the strip.
  if (other.length) {
    let x = left;
    other.sort(natural).forEach((ident) => {
      cells[ident] = { x, y };
      x += PITCH;
    });
    width = Math.max(width, x - GAP);
    y += bandHeight(1) + UNIT_GAP;
  }

  const height = Math.max(0, y - UNIT_GAP);
  return { cells, units, width: Math.max(width, 0), height };
}

/** Port idents from a device's portIds, which are `<deviceId>#<ident>`. */
export function identsOf(portIds: string[] | undefined): string[] {
  return (portIds ?? []).map((id) => id.slice(id.lastIndexOf("#") + 1));
}
