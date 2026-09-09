import type { SnapshotMeta } from "./types";

/**
 * What this run could NOT see, assembled from what the collector already
 * records and the UI previously threw away.
 *
 * The whole argument for this tool is that it explains itself. A map drawn from
 * partial input that does not SAY it was partial is the exact failure it exists
 * to avoid — and it is the more dangerous failure, because a map with a hole in
 * it looks just like a map of a network that has a hole in it.
 *
 * Everything here is already in the snapshot. Nothing is recomputed, nothing
 * costs a request; it was simply not being read.
 */

export type CoverageLevel = "ok" | "info" | "warn" | "error";

export type CoverageItem = {
  level: CoverageLevel;
  group: "reads" | "collection" | "evidence" | "identity";
  title: string;
  /** What it means for the map, not what the field is called. */
  detail: string;
};

export type Coverage = {
  level: CoverageLevel;
  /** One sentence, concrete. Empty when there is nothing to report. */
  headline: string;
  items: CoverageItem[];
  counts: { error: number; warn: number; info: number };
};

const RANK: Record<CoverageLevel, number> = { ok: 0, info: 1, warn: 2, error: 3 };

/**
 * Sources whose job is to ARGUE AGAINST a link, not to propose one.
 *
 * These carry negative weight in score.py, so finding nothing means the network
 * is clean -- no down ports claimed as endpoints, nothing blocked by spanning
 * tree, no cross-venue links. Describing them the same way as a source that
 * proposes links would tell the reader that something is missing when in fact
 * nothing is wrong, which is precisely the misreading this panel exists to
 * prevent.
 */
const CONTRADICTION_SOURCES: Record<string, string> = {
  "port.down": "no link was claimed on a port that is down",
  "port.stp.blocked": "nothing was reported as blocked by spanning tree",
  "venue.cross": "no link crossed a venue boundary",
  "identity.collision": "no two devices claimed the same identity",
  "port.contention": "no port was claimed by two different peers",
  "mac.device.multiport": "no device appeared on more than one port of a switch",
};

/**
 * Evidence sources that legitimately contribute nothing, and why.
 *
 * Without this a clean run looks alarming: half a dozen sources reporting zero
 * on a perfectly healthy venue. Naming the expected ones is what keeps the rest
 * of the list worth reading.
 */
const EXPECTED_EMPTY: Record<string, string> = {
  "lldp.ap.tlv":
    "Access points only report LLDP after a write that refreshes their cache, " +
    "which a read-only tool will not do. Expected to be empty.",
  "mesh.uplink": "No mesh links here. Expected on a fully wired venue.",
  "mesh.r1.edge": "No mesh links here. Expected on a fully wired venue.",
  "lldp.switch.name":
    "Every neighbour resolved by MAC, so nothing had to fall back to matching " +
    "on name. This being empty is a good sign.",
  "lldp.switch.name.ambiguous":
    "No neighbour name was ambiguous between two devices. A good sign.",
};

/**
 * Give a fragment a full stop so it can be joined to the next sentence.
 *
 * Error strings come from R1, from exceptions, and from our own code, and about
 * half of them end without punctuation -- which read as "R1 returned 500 for
 * the MAC table query Whatever this read covers is simply absent".
 */
function sentence(text: string): string {
  const trimmed = (text ?? "").trim();
  if (!trimmed) return "";
  return /[.!?:;]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

function pretty(id: string): string {
  // Source ids carry a venue id after a colon; it is noise in a list.
  return id.split(":")[0];
}

export function assessCoverage(meta: SnapshotMeta | null): Coverage {
  const items: CoverageItem[] = [];
  if (!meta) {
    return { level: "ok", headline: "", items, counts: { error: 0, warn: 0, info: 0 } };
  }

  // 1. Reads that did not come back. The map is missing data outright.
  (meta.sources ?? [])
    .filter((source) => source.status && source.status !== "ok")
    .forEach((source) =>
      items.push({
        level: "error",
        group: "reads",
        title: `${pretty(source.id)} did not return`,
        detail:
          sentence(source.error || `Status "${source.status}"`) +
          " Whatever this read covers is simply absent from the map — not " +
          "absent from the network.",
      }),
    );

  // 2. Collection that came back short. Silently truncated in exactly the dense
  //    places that matter most.
  const completeness = (meta.completeness ?? {}) as {
    incomplete?: number;
    expected?: number;
    collected?: number;
    shortfalls?: unknown[];
  };
  const shortfalls = completeness.shortfalls ?? [];
  if ((completeness.incomplete ?? 0) > 0 || shortfalls.length) {
    const expected = completeness.expected ?? 0;
    const collected = completeness.collected ?? 0;
    items.push({
      level: "warn",
      group: "collection",
      title: `${completeness.incomplete || shortfalls.length} query returned fewer rows than RUCKUS ONE declared`,
      detail:
        (expected
          ? `Collected ${collected.toLocaleString()} of ${expected.toLocaleString()} rows. `
          : "") +
        "A truncated MAC table degrades inference exactly where the network is " +
        "densest, so treat sparse areas of this map with suspicion.",
    });
  }

  // 3. Whatever the collector chose to say for itself.
  (meta.warnings ?? []).forEach((warning) => {
    const [first, ...rest] = warning.split(". ");
    items.push({
      level: "warn",
      group: "collection",
      title: sentence(first),
      // A single-sentence warning would otherwise repeat itself in both slots.
      detail: rest.length ? sentence(rest.join(". ")) : "",
    });
  });

  // 4. Evidence sources. A failure here is a missing kind of proof; a zero is
  //    worth showing too, since "why is there no edge here?" is the question
  //    this tool exists to answer.
  const correlation = meta.correlation;
  (correlation?.sourcesFailed ?? []).forEach((source) =>
    items.push({
      level: "error",
      group: "evidence",
      title: `Evidence source ${pretty(source.id)} failed`,
      detail:
        (source.error || "No reason given.") +
        " Links that would have relied on it are weaker than they should be, " +
        "or missing.",
    }),
  );
  (correlation?.sourcesSkipped ?? []).forEach((source) =>
    items.push({
      level: "info",
      group: "evidence",
      title: `Evidence source ${pretty(source.id)} did not run`,
      detail: sentence(source.reason || "Skipped for this run"),
    }),
  );
  const quiet = (correlation?.sourcesRun ?? []).filter((source) => !source.claims);
  // Contradiction sources are rolled into ONE line. Six separate "found
  // nothing" entries for six clean results buries the one that matters.
  const clean = quiet.filter((source) => pretty(source.id) in CONTRADICTION_SOURCES);
  if (clean.length) {
    items.push({
      level: "info",
      group: "evidence",
      title: `${clean.length} check${clean.length === 1 ? "" : "s"} for evidence AGAINST a link found none`,
      detail:
        "Nothing argued against any link here: " +
        clean.map((s) => CONTRADICTION_SOURCES[pretty(s.id)]).join("; ") +
        ". These carry negative weight, so finding nothing is a good result, " +
        "not a missing one.",
    });
  }
  quiet
    .filter((source) => !(pretty(source.id) in CONTRADICTION_SOURCES))
    .forEach((source) =>
      items.push({
        level: "info",
        group: "evidence",
        title: `${pretty(source.id)} found nothing`,
        detail:
          EXPECTED_EMPTY[pretty(source.id)] ??
          "It ran and produced no evidence. Not an error, but any link that " +
            "would have depended on it rests on the other sources alone.",
      }),
    );

  // 5. Identity. A rejected name resolution is a link that had to be argued
  //    some other way, or not at all.
  const collisions = correlation?.aliasCollisions ?? [];
  if (collisions.length) {
    items.push({
      level: "info",
      group: "identity",
      title: `${collisions.length} name${collisions.length === 1 ? "" : "s"} matched more than one device`,
      detail:
        collisions
          .slice(0, 3)
          .map((c) => `"${c.key}"`)
          .join(", ") +
        (collisions.length > 3 ? ", …" : "") +
        ". A neighbour advertising one of these could not be resolved by name " +
        "alone, so those links rest on MAC evidence or are absent.",
    });
  }

  const counts = {
    error: items.filter((i) => i.level === "error").length,
    warn: items.filter((i) => i.level === "warn").length,
    info: items.filter((i) => i.level === "info").length,
  };
  const level = items.reduce<CoverageLevel>(
    (worst, item) => (RANK[item.level] > RANK[worst] ? item.level : worst),
    "ok",
  );

  // The headline names the worst thing concretely. "Some issues were found" is
  // not worth the pixels.
  const worst = items.find((item) => item.level === level);
  const headline =
    level === "ok" || level === "info"
      ? ""
      : counts.error + counts.warn === 1
        ? worst?.title ?? ""
        : `${worst?.title ?? ""} (+${counts.error + counts.warn - 1} more)`;

  return { level, headline, items, counts };
}
