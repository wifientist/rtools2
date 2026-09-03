"""
Regression test: findings must name every entity they count.

Checks used to truncate their evidence lists at generation time -- `sorted(
switches)[:8]` and friends across ~25 sites. A finding reading "VLAN 1300 has no
spanning-tree instance on 31 switch(es)" therefore named 8 of them and discarded
the other 23 before storage, so no export could recover them: report.json returns
the stored result verbatim, and the CSV serialises the same evidence dict. The
CSV's own docstring says it exists for "handing a list to whoever does the
patching"; it could hand over 8 of 31.

Lists are now complete, bounded only by framework.MAX_EVIDENCE_LIST, and a
finding records when that backstop bites so a cut list can never be mistaken for
a whole one.

Read-only by construction: synthetic configs, no R1, no stored data.

Usage:
    docker compose exec backend python scripts/test_wiredwiz_evidence.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.wiredwiz.checks.framework import (
    CheckContext, Finding, IcxConfig, MAX_EVIDENCE_LIST,
)
from services.wiredwiz.checks.config_checks import stp_vlan_uncovered

failures = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        failures.append(msg)


# ── a fleet where VLAN 1300 lacks spanning-tree on 31 of 51 switches ─────────
WITH_STP = "vlan 1300 name DATA\n spanning-tree 802-1w\n!\n"
WITHOUT_STP = "vlan 1300 name DATA\n untagged ethe 1/1/1\n!\n"

configs = {}
for i in range(31):
    n = f"SW-NOSTP-{i:03d}"
    configs[n] = IcxConfig(n, n, "ICX7150", WITHOUT_STP)
for i in range(20):
    n = f"SW-OK-{i:03d}"
    configs[n] = IcxConfig(n, n, "ICX7150", WITH_STP)

snap = {"takenAt": "t", "takenAtEpoch": time.time(), "switches": [], "ports": [],
        "macs": [], "venues": {}, "completeness": {"incomplete": 0}}
ctx = CheckContext.build([snap], configs=configs)

print("\n=== the reported case: 31 of 51 switches ===")
findings = list(stp_vlan_uncovered(ctx))
check(len(findings) == 1, f"one finding for VLAN 1300 (got {len(findings)})")
f = findings[0].as_dict()
ev = f["evidence"]
print(f"  title: {f['title']}")
check(ev["switchesWithout"] == 31, f"counts 31 switches (got {ev['switchesWithout']})")
check(ev["switchesWithStp"] == 20, f"counts 20 covered (got {ev['switchesWithStp']})")
named = ev.get("switchesMissingStp") or []
check(len(named) == 31, f"NAMES all 31, not 8 (got {len(named)})")
check(len(named) == ev["switchesWithout"], "the named list and the count agree")
check(named == sorted(named), "list is sorted, so it is diffable between runs")
check("SW-NOSTP-030" in named, "the 31st switch is present, not dropped off the end")
check("evidenceCapped" not in f, "31 entries is far below the backstop, so nothing is flagged")

print("\n=== the backstop still protects storage ===")
big = Finding("x", "t", "warning", "c", "e", "d",
              {"ports": [f"1/1/{i}" for i in range(750)]})
d = big.as_dict()
check(len(d["evidence"]["ports"]) == MAX_EVIDENCE_LIST,
      f"a 750-entry list is capped at {MAX_EVIDENCE_LIST} (got {len(d['evidence']['ports'])})")
check(d.get("evidenceCapped", {}).get("ports") == {"shown": 500, "total": 750},
      "and the finding records that it was cut, with the true total")

small = Finding("x", "t", "warning", "c", "e", "d", {"switches": ["a", "b"]})
check("evidenceCapped" not in small.as_dict(),
      "an uncapped finding carries no capping flag")

print("\n=== the CSV can hand over the whole list ===")
sys.path.insert(0, str(Path(__file__).parent.parent))
import csv as _csv
import io as _io
# exercise the same row-building the exporter uses
ev_lists = [v for v in ev.values()
            if isinstance(v, list) and v and all(isinstance(x, (str, int, float)) for x in v)]
affected, seen = [], set()
for v in ev_lists:
    for x in v:
        if str(x) not in seen:
            seen.add(str(x)); affected.append(str(x))
check(len(affected) >= 31, f"affected column carries >= 31 entries (got {len(affected)})")
row = "|".join(affected)
check(len(row.split("|")) == len(affected), "pipe-separated and splittable back into a work list")

print(f"\n{'FAILED: ' + '; '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
