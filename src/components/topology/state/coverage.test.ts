/**
 * What the map admits it could not see.
 *
 * The failure this guards against is silence: a run with a failed read or a
 * truncated MAC table drawing a confident map and saying nothing. So the tests
 * are mostly "does it speak up", plus the one distinction that is easy to get
 * backwards — a source whose job is to argue AGAINST links finding nothing is
 * good news, and must never be reported as missing evidence.
 *
 *   docker compose -f docker-compose.dev.yml exec -T frontend \
 *       npx tsx src/components/topology/state/coverage.test.ts
 */
import { assessCoverage } from "./coverage";
import type { SnapshotMeta } from "./types";

let passed = 0;
const failures: string[] = [];
function check(name: string, ok: boolean, detail = "") {
  if (ok) passed += 1;
  else failures.push(name);
  console.log(`  ${ok ? "ok  " : "FAIL"} ${name}${ok || !detail ? "" : `\n       ${detail}`}`);
}

const meta = (over: Partial<SnapshotMeta> = {}): SnapshotMeta =>
  ({
    takenAt: "", takenAtEpoch: 0, tenantId: "t", scopeVenueIds: [], venues: {},
    counts: {}, sources: [], completeness: {}, warnings: [], elapsedSeconds: 1,
    deep: false, ...over,
  }) as SnapshotMeta;

console.log("\na clean run says nothing");
{
  const c = assessCoverage(meta());
  check("level is ok", c.level === "ok", c.level);
  check("no headline", c.headline === "", c.headline);
  check("no items", c.items.length === 0);
}
check("a missing snapshot does not throw", assessCoverage(null).level === "ok");

console.log("\na read that failed is an ERROR, and says what it costs");
{
  const c = assessCoverage(meta({
    sources: [{ id: "macs", status: "error", rows: 0, elapsedMs: 12, error: "500 from R1" }],
  }));
  check("level is error", c.level === "error", c.level);
  check("headline names it", c.headline.includes("macs"), c.headline);
  check("carries the reason", c.items[0].detail.includes("500 from R1"));
  check("says what it means for the map",
        c.items[0].detail.includes("absent from the map"), c.items[0].detail);
}

console.log("\na truncated collection is a WARNING");
{
  const c = assessCoverage(meta({
    completeness: { incomplete: 2, expected: 10000, collected: 8400, shortfalls: [{}, {}] },
  }));
  check("level is warn", c.level === "warn", c.level);
  check("quotes both numbers",
        c.items[0].detail.includes("8,400") && c.items[0].detail.includes("10,000"),
        c.items[0].detail);
  check("warns where it matters", c.items[0].detail.includes("densest"));
}

console.log("\nthe collector's own warning is surfaced verbatim");
{
  const real = "Skipped per-switch routes/SVIs for 112 switches (re-run with deep=true). WAN inference will fall back to LLDP and cloud-port signals.";
  const c = assessCoverage(meta({ warnings: [real] }));
  check("level is warn", c.level === "warn", c.level);
  check("headline is the first sentence",
        c.headline.startsWith("Skipped per-switch routes/SVIs for 112 switches"), c.headline);
  check("the consequence survives into the detail",
        c.items[0].detail.includes("WAN inference will fall back"), c.items[0].detail);
}

console.log("\ncontradiction sources finding nothing is GOOD NEWS, not missing evidence");
{
  const c = assessCoverage(meta({
    correlation: {
      claims: 1, links: 1, byTier: {}, byKind: {}, byDirectionality: {},
      externalDevices: 0, outOfScopeDevices: 0, unattachedAps: 0, elapsedSeconds: 1,
      sourcesRun: [
        { id: "port.down", claims: 0 },
        { id: "port.stp.blocked", claims: 0 },
        { id: "venue.cross", claims: 0 },
        { id: "lldp.switch.mac", claims: 40 },
      ],
      sourcesFailed: [],
    },
  }));
  check("does not raise the level", c.level === "info", c.level);
  check("no headline for info alone", c.headline === "", c.headline);
  check("rolled into ONE line, not three",
        c.items.filter((i) => i.group === "evidence").length === 1,
        `${c.items.length} items`);
  const line = c.items[0];
  check("framed as a good result", line.detail.includes("good result"), line.detail);
  check("never says evidence is missing",
        !line.detail.includes("depended on it"), line.detail);
  check("a source WITH claims is not mentioned",
        !JSON.stringify(c.items).includes("lldp.switch.mac"));
}

console.log("\na proposing source finding nothing is explained, not just flagged");
{
  const c = assessCoverage(meta({
    correlation: {
      claims: 0, links: 0, byTier: {}, byKind: {}, byDirectionality: {},
      externalDevices: 0, outOfScopeDevices: 0, unattachedAps: 0, elapsedSeconds: 1,
      sourcesRun: [{ id: "lldp.ap.tlv", claims: 0 }, { id: "some.new.source", claims: 0 }],
      sourcesFailed: [],
    },
  }));
  check("a known-empty source explains WHY",
        c.items.some((i) => i.detail.includes("read-only tool will not do")));
  check("an unknown one still gets an honest line",
        c.items.some((i) => i.detail.includes("rests on the other sources alone")));
  check("neither is escalated above info", c.level === "info", c.level);
}

console.log("\nsentences are joined, not run together");
{
  const c = assessCoverage(meta({
    sources: [{ id: "macs", status: "error", rows: 0, elapsedMs: 1, error: "R1 returned 500 for the MAC table query" }],
  }));
  check("a full stop is added to an unpunctuated error",
        c.items[0].detail.includes("query. Whatever"), c.items[0].detail);
}
{
  const c = assessCoverage(meta({
    sources: [{ id: "macs", status: "error", rows: 0, elapsedMs: 1, error: "Timed out." }],
  }));
  check("existing punctuation is not doubled",
        c.items[0].detail.startsWith("Timed out. Whatever") && !c.items[0].detail.includes(".."),
        c.items[0].detail);
}
{
  const c = assessCoverage(meta({ warnings: ["One sentence only."] }));
  check("a single-sentence warning does not repeat itself",
        c.items[0].title === "One sentence only." && c.items[0].detail === "",
        `${c.items[0].title} / ${c.items[0].detail}`);
}

console.log("\na failed evidence source is an error");
{
  const c = assessCoverage(meta({
    correlation: {
      claims: 0, links: 0, byTier: {}, byKind: {}, byDirectionality: {},
      externalDevices: 0, outOfScopeDevices: 0, unattachedAps: 0, elapsedSeconds: 1,
      sourcesRun: [], sourcesFailed: [{ id: "mactable", error: "boom" }],
    },
  }));
  check("level is error", c.level === "error", c.level);
  check("says links are weaker for it", c.items[0].detail.includes("weaker"));
}

console.log("\nambiguous names are reported with examples");
{
  const c = assessCoverage(meta({
    correlation: {
      claims: 0, links: 0, byTier: {}, byKind: {}, byDirectionality: {},
      externalDevices: 0, outOfScopeDevices: 0, unattachedAps: 0, elapsedSeconds: 1,
      sourcesRun: [], sourcesFailed: [],
      aliasCollisions: [1, 2, 3, 4].map((n) => ({ kind: "name", key: `sw-${n}`, held: "a", rejected: "b" })),
    },
  }));
  check("counted", c.items[0].title.startsWith("4 names"), c.items[0].title);
  check("shows a few, then elides", c.items[0].detail.includes("…"), c.items[0].detail);
}

console.log("\nseverity and the headline");
{
  const c = assessCoverage(meta({
    warnings: ["Something was skipped. It matters."],
    sources: [{ id: "macs", status: "error", rows: 0, elapsedMs: 1 }],
  }));
  check("the worst level wins", c.level === "error", c.level);
  check("headline leads with the worst thing", c.headline.startsWith("macs did not return"), c.headline);
  check("and counts the rest", c.headline.includes("+1 more"), c.headline);
  check("counts are separated",
        c.counts.error === 1 && c.counts.warn === 1, JSON.stringify(c.counts));
}

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
