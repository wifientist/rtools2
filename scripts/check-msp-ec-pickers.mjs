#!/usr/bin/env node
/**
 * Frontend MSP-EC selection regression check.
 *
 * THE OTHER HALF OF THE TENANT BUG
 *
 * resolve_tenant_id() made the backend refuse an MSP request with no EC.
 * That is correct, and it is only half: a page that never lets you CHOOSE an
 * EC then fails with a 400 instead of silently reporting on the MSP's own
 * tenant. Better, but still broken for the user.
 *
 * Five pages were passing a hardcoded null:
 *
 *     const effectiveTenantId = needsEcSelection ? null : ...
 *
 * which is the frontend spelling of the same bug — the EC is not merely
 * unselected, there is no way to select one.
 *
 * Structural on purpose. The fault spread by copy-paste, each new tool
 * starting from the last, which is what a grep catches and a behavioural
 * test does not.
 *
 * Runs in the frontend container because the backend image mounts only ./api
 * and cannot see src/.
 *
 *   docker compose exec frontend node scripts/check-msp-ec-pickers.mjs
 */
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const PAGES = "src/pages";

// A tenant that is null whenever the controller is an MSP.
const NULL_ON_MSP =
  /(needsEcSelection|activeControllerSubtype\s*===\s*"MSP")\s*\r?\n?\s*\?\s*null/;

// Pages that scope work to one tenant and must offer a way to pick it.
const MUST_OFFER_EC = [
  "APPopAndSwap.tsx", "APPortConfig.tsx", "APRegroup.tsx", "APRename.tsx",
  "BulkAPTagging.tsx", "BulkWlanEdit.tsx", "CloudpathImport.tsx",
  "DangerZone.tsx", "PerUnitSSID.tsx", "Topology.tsx", "WiredWiz.tsx",
];

// Topology and WiredWiz gate inline rather than via the shared component.
// Accepted: they gate correctly, which is the point.
const EC_MARKERS = ["MspEcPicker", "SingleEcSelector", "EcSelector"];

let failures = 0;
const check = (label, ok, detail = "") => {
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${label}${detail ? `  -- ${detail}` : ""}`);
  if (!ok) failures++;
};

console.log("MSP-EC selection on tenant-scoped pages\n");

const offenders = readdirSync(PAGES)
  .filter((f) => f.endsWith(".tsx"))
  .filter((f) => NULL_ON_MSP.test(readFileSync(join(PAGES, f), "utf8")));
check(
  "no page hardcodes a null tenant for an MSP controller",
  offenders.length === 0,
  offenders.join(", "),
);

const missing = MUST_OFFER_EC.filter((name) => {
  const p = join(PAGES, name);
  if (!existsSync(p)) return true;
  const src = readFileSync(p, "utf8");
  return !EC_MARKERS.some((m) => src.includes(m));
});
check(
  "every tenant-scoped page offers a way to choose the EC",
  missing.length === 0,
  missing.join(", "),
);

console.log(`\n${failures ? `FAILED (${failures})` : "All checks passed"}`);
process.exit(failures ? 1 : 0);
