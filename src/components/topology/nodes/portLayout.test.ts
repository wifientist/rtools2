/**
 * Port chips laid out like the faceplate.
 *
 * The tests pin the physical arrangement rather than pixel values where they
 * can: "1/1/2 is directly below 1/1/1" survives a change to the chip size;
 * "1/1/2 is at y=15" does not.
 *
 *   docker compose -f docker-compose.dev.yml exec -T frontend \
 *       npx tsx src/components/topology/nodes/portLayout.test.ts
 */
import { CHIP, GAP, MODULE_GAP, identsOf, panelLayout } from "./portLayout";

let passed = 0;
const failures: string[] = [];
function check(name: string, ok: boolean, detail = "") {
  if (ok) passed += 1;
  else failures.push(name);
  console.log(`  ${ok ? "ok  " : "FAIL"} ${name}${ok || !detail ? "" : `\n       ${detail}`}`);
}

const range = (unit: number, module: number, n: number) =>
  Array.from({ length: n }, (_, i) => `${unit}/${module}/${i + 1}`);

/** An ICX8200-48PF: 48 access ports and 4 uplinks. The commonest shape measured. */
const icx48 = [...range(1, 1, 48), ...range(1, 2, 4)];

console.log("\nthe faceplate order the request describes");
{
  const { cells } = panelLayout(icx48);
  const a = cells["1/1/1"], b = cells["1/1/2"], c = cells["1/1/3"], d = cells["1/1/4"];
  check("1/1/1 is top left", a.x === 0 && a.y === 0, JSON.stringify(a));
  check("1/1/2 is directly below 1/1/1", b.x === a.x && b.y > a.y, JSON.stringify(b));
  check("1/1/3 is directly right of 1/1/1", c.y === a.y && c.x > a.x, JSON.stringify(c));
  check("1/1/4 is below 1/1/3", d.x === c.x && d.y === b.y, JSON.stringify(d));
  check("odd ports all on the top row",
        range(1, 1, 48).filter((_, i) => i % 2 === 0).every((id) => cells[id].y === 0));
  check("48 ports make 24 columns", cells["1/1/48"].x === 23 * (CHIP + GAP),
        JSON.stringify(cells["1/1/48"]));
}

console.log("\nR1's lexicographic order does not matter");
{
  const served = ["1/1/1", "1/1/10", "1/1/11", "1/1/12", "1/1/2", "1/1/3",
                  "1/1/4", "1/1/5", "1/1/6", "1/1/7", "1/1/8", "1/1/9"];
  const numeric = range(1, 1, 12);
  check("same cells either way",
        JSON.stringify(panelLayout(served).cells, Object.keys(panelLayout(numeric).cells).sort()) ===
        JSON.stringify(panelLayout(numeric).cells, Object.keys(panelLayout(numeric).cells).sort()));
  check("1/1/10 lands in column 4, not after 1/1/1",
        panelLayout(served).cells["1/1/10"].x === 4 * (CHIP + GAP));
}

console.log("\nmodules are separate blocks, a small gap apart");
{
  const { cells } = panelLayout(icx48);
  const lastOfFirst = cells["1/1/47"];
  const firstOfSecond = cells["1/2/1"];
  const separation = firstOfSecond.x - (lastOfFirst.x + CHIP);
  check("1/2/1 starts to the right of module 1", firstOfSecond.x > lastOfFirst.x);
  check("on the top row, like 1/1/1", firstOfSecond.y === 0);
  check("the separation is wider than a normal gap", separation > GAP, `${separation}px`);
  check("but still small", separation === MODULE_GAP && separation < CHIP, `${separation}px`);
  check("1/2/2 is below 1/2/1", cells["1/2/2"].x === firstOfSecond.x && cells["1/2/2"].y > 0);
}
{
  const icx7150 = [...range(1, 1, 24), ...range(1, 2, 2), ...range(1, 3, 4)];
  const { cells } = panelLayout(icx7150);
  check("three modules run left to right: 1/1 < 1/2 < 1/3",
        cells["1/1/23"].x < cells["1/2/1"].x && cells["1/2/1"].x < cells["1/3/1"].x);
  check("each separated by the module gap",
        cells["1/3/1"].x - (cells["1/2/1"].x + CHIP) === MODULE_GAP,
        `${cells["1/3/1"].x - (cells["1/2/1"].x + CHIP)}px`);
}

console.log("\na stack gets one band per unit");
{
  const stack = [...range(1, 1, 48), ...range(1, 2, 8), ...range(2, 1, 48), ...range(2, 2, 8)];
  const layout = panelLayout(stack);
  const u1 = layout.cells["1/1/1"], u2 = layout.cells["2/1/1"];
  check("unit 2 is below unit 1", u2.y > layout.cells["1/1/2"].y, JSON.stringify(u2));
  check("and lines up with it", u2.x === u1.x);
  check("both units labelled", layout.units.map((u) => u.unit).join() === "1,2");
  check("a standalone switch has no unit labels", panelLayout(icx48).units.length === 0);
  check("a standalone switch starts at the edge, a stack leaves a gutter",
        panelLayout(icx48).cells["1/1/1"].x === 0 && u1.x > 0);
}

console.log("\na port R1 omits leaves a hole instead of shifting the rest");
{
  const { cells } = panelLayout(["1/1/1", "1/1/4"]);
  check("1/1/4 keeps its faceplate position",
        cells["1/1/4"].x === CHIP + GAP && cells["1/1/4"].y === CHIP + GAP,
        JSON.stringify(cells["1/1/4"]));
}

console.log("\nnothing is dropped, nothing overlaps");
{
  const weird = [...icx48, "mgmt1", "lag10", "lag9"];
  const layout = panelLayout(weird);
  check("every ident gets a cell", weird.every((id) => layout.cells[id]),
        weird.filter((id) => !layout.cells[id]).join());
  const keys = Object.values(layout.cells).map((c) => `${c.x},${c.y}`);
  check("no two ports share a cell", new Set(keys).size === keys.length);
  check("odd idents are naturally ordered", layout.cells["lag9"].x < layout.cells["lag10"].x);
  check("duplicates are drawn once", Object.keys(panelLayout([...icx48, "1/1/1"]).cells).length === 52);
}

console.log("\nthe reported size contains every chip");
for (const [name, idents] of [
  ["48+4", icx48],
  ["stack of 5", [1, 2, 3, 4, 5].flatMap((u) => [...range(u, 1, 48), ...range(u, 2, 4)])],
  ["with an odd ident", [...icx48, "mgmt1"]],
] as [string, string[]][]) {
  const layout = panelLayout(idents);
  const fits = Object.values(layout.cells).every(
    (c) => c.x + CHIP <= layout.width && c.y + CHIP <= layout.height);
  check(`${name}: fits`, fits, `${layout.width}x${layout.height}`);
}
check("an empty list is an empty panel", panelLayout([]).width === 0 && panelLayout([]).height === 0);

console.log("\nportIds carry the device id, identsOf strips it");
check("strips the prefix",
      identsOf(["sw:10f068090a72#1/1/1", "sw:10f068090a72#1/2/3"]).join() === "1/1/1,1/2/3");
check("tolerates undefined", identsOf(undefined).length === 0);

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
