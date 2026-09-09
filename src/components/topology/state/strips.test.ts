/**
 * Room made for an opened port strip.
 *
 * The property that matters most is not "things move" but "things move back":
 * the offsets are recomputed from the layout every render rather than
 * accumulated, so open/close has to be exactly reversible or a map drifts
 * downward every time someone inspects a switch.
 *
 *   docker compose -f docker-compose.dev.yml exec -T frontend \
 *       npx tsx src/components/topology/state/strips.test.ts
 */
import { stripOffsets, withStripOffsets } from "./strips";
import type { Box } from "./strips";

let passed = 0;
const failures: string[] = [];
function check(name: string, ok: boolean, detail = "") {
  if (ok) passed += 1;
  else failures.push(name);
  console.log(`  ${ok ? "ok  " : "FAIL"} ${name}${ok || !detail ? "" : `\n       ${detail}`}`);
}

const box = (x: number, y: number, w = 170, h = 56): Box => ({ x, y, w, h });

/** A tidy three-rank column: one node per rank, stacked. */
const column: Record<string, Box> = {
  top: box(0, 0),
  mid: box(0, 160),
  low: box(0, 320),
};

console.log("\nnothing open changes nothing");
check("no growth, no offsets", Object.keys(stripOffsets(column, {})).length === 0);
check("zero growth is not growth",
      Object.keys(stripOffsets(column, { top: 0 })).length === 0);
{
  const positions = { a: { x: 1, y: 2 } };
  check("withStripOffsets returns the SAME object when idle",
        withStripOffsets(positions, {}) === positions);
}

console.log("\nopening one strip pushes what is below it, and only that");
{
  const off = stripOffsets(column, { mid: 200 });
  check("the grown node itself does not move", !off.top && !(off.mid > 0), JSON.stringify(off));
  check("the node above is untouched", (off.top ?? 0) === 0);
  check("the node below moves by exactly the growth", off.low === 200, JSON.stringify(off));
}

console.log("\na node beside the column is left alone");
{
  const beside = { ...column, aside: box(900, 320) };
  const off = stripOffsets(beside, { mid: 200 });
  check("no horizontal overlap, no push", (off.aside ?? 0) === 0, JSON.stringify(off));
  check("the one below still moves", off.low === 200);
}

console.log("\nthe push cascades to what the pushed node would hit");
{
  // `far` does not overlap `mid` horizontally, but DOES overlap `low`, which is
  // being pushed onto it. Without widening the band, low would land on far.
  const graph: Record<string, Box> = {
    mid: box(0, 0, 100),
    low: box(60, 200, 100),
    far: box(140, 400, 100),
  };
  const off = stripOffsets(graph, { mid: 300 });
  check("the directly-overlapping node moves", off.low === 300, JSON.stringify(off));
  check("and so does the one IT would land on", off.far === 300, JSON.stringify(off));
}

console.log("\ntwo strips open at once stack their offsets");
{
  const off = stripOffsets(column, { top: 100, mid: 200 });
  check("the middle node clears the top strip only", off.mid === 100, JSON.stringify(off));
  check("the bottom node clears both", off.low === 300, JSON.stringify(off));
}

console.log("\na hand-placed node is never moved");
{
  const off = stripOffsets(column, { mid: 200 }, { low: { x: 0, y: 320 } });
  check("pinned nodes stay pinned", (off.low ?? 0) === 0, JSON.stringify(off));
}

console.log("\nreversibility — the point of computing this from scratch");
{
  const positions = { top: { x: 0, y: 0 }, mid: { x: 0, y: 160 }, low: { x: 0, y: 320 } };
  const opened = withStripOffsets(positions, stripOffsets(column, { mid: 200 }));
  check("opening moved something", opened.low.y === 520, JSON.stringify(opened.low));
  const closed = withStripOffsets(positions, stripOffsets(column, {}));
  check("closing restores the layout exactly",
        JSON.stringify(closed) === JSON.stringify(positions), JSON.stringify(closed));

  // Fifty open/close cycles must not drift, because each is computed from the
  // same layout rather than applied to the last result. Ends closed.
  let latest: Record<string, { x: number; y: number }> = positions;
  for (let i = 0; i < 100; i += 1) {
    latest = withStripOffsets(positions, stripOffsets(column, i % 2 === 0 ? { mid: 200 } : {}));
  }
  check("fifty open/close cycles drift nothing",
        JSON.stringify(latest) === JSON.stringify(positions), JSON.stringify(latest));
}

console.log("\ndeterminism and scale");
{
  // Compared as sorted entries, not as JSON: a map's key ORDER is allowed to
  // differ, its contents are not.
  const canonical = (o: Record<string, number>) =>
    Object.entries(o).sort(([x], [y]) => x.localeCompare(y)).map(([k, v]) => `${k}=${v}`).join(",");
  const forward = canonical(stripOffsets(column, { top: 100, mid: 200 }));
  const reversed = canonical(stripOffsets(
    { low: column.low, mid: column.mid, top: column.top },
    { mid: 200, top: 100 },
  ));
  check("key order does not change the answer", forward === reversed,
        `${forward} vs ${reversed}`);

  const big: Record<string, Box> = {};
  for (let i = 0; i < 2000; i += 1) big[`n${i}`] = box((i % 40) * 200, Math.floor(i / 40) * 160);
  const t0 = Date.now();
  const off = stripOffsets(big, { n0: 300 });
  const ms = Date.now() - t0;
  check(`2000 nodes resolve quickly (${ms}ms)`, ms < 400);
  check("and only the affected column moved",
        Object.keys(off).length > 0 && Object.keys(off).length < 2000,
        `${Object.keys(off).length} of 2000`);
}

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
