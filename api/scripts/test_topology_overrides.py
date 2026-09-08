"""
Human verdicts: the two paths that apply one must agree, and re-lensing must be
idempotent.

A verdict is applied in two places. At DISCOVERY it is applied to model objects
(score.apply_overrides). On every READ it is applied to stored dicts
(overrides.relens), because the normal way to record a verdict is to look at
the map and press confirm -- which happens after the snapshot was taken.

Two code paths applying the same rule is exactly how a tool starts lying: the
map says Confirmed and the export says Probable, or a verdict shows on screen
and vanishes on the next discovery. These tests pin them together.

    docker compose -f docker-compose.dev.yml exec -T backend \
        python scripts/test_topology_overrides.py
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.topology import overrides as ov            # noqa: E402
from services.topology import score as score_mod         # noqa: E402
from services.topology.model import Endpoint, Evidence, Link, Override  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not cond else ""))


def build_link():
    """A probable link with one supporting and one contradicting row."""
    link = Link(
        id="lnk1",
        a=Endpoint(device_id="sw:aabbccddeeff", port_id="sw:aabbccddeeff#1/1/1", ident="1/1/1"),
        b=Endpoint(device_id="sw:112233445566", port_id="sw:112233445566#1/2/3", ident="1/2/3"),
        kind="ethernet", score=1.2, confidence=0.77, tier="probable",
        tier_reason="One side names the other.", directionality="a-only",
        evidence=[
            Evidence(source="lldp.switch.name", kind="support", claim="A names B.",
                     weight=1.8, base_weight=1.8),
            Evidence(source="port.oper.down", kind="contradict", claim="B's port is down.",
                     weight=-2.5, base_weight=-2.5),
        ],
    )
    return link


DEVICES = [{"id": "sw:aabbccddeeff", "serial": "SERIALA", "mac": "aabbccddeeff"},
           {"id": "sw:112233445566", "serial": "", "mac": "112233445566"}]

IDENTITY = ov.identity_map(DEVICES)


def stored_with(verdict):
    key = ov.link_key(ov.endpoint_key("SERIALA", "1/1/1"),
                      ov.endpoint_key("112233445566", "1/2/3"))
    return key, {"links": {key: {"key": key, "verdict": verdict, "by": "tech@example.com",
                                 "at": "2026-09-08T10:00:00Z", "note": "traced by hand"}},
                 "wan": {}}


print("\nidentity and keying")
check("serial wins over MAC when both exist", IDENTITY["sw:aabbccddeeff"] == "SERIALA")
check("MAC is used when there is no serial", IDENTITY["sw:112233445566"] == "112233445566")
_key, _ = stored_with("confirm")
check("the dict path derives the same key the correlator does",
      ov.key_for_link(build_link().to_dict(), IDENTITY) == _key,
      f"{ov.key_for_link(build_link().to_dict(), IDENTITY)!r} != {_key!r}")
check("the key is order-independent",
      ov.link_key("b#2", "a#1") == ov.link_key("a#1", "b#2"))


for verdict in ("confirm", "reject", "assert"):
    print(f"\nverdict '{verdict}': object path vs dict path")
    key, stored = stored_with(verdict)

    obj = build_link()
    score_mod.apply_overrides([obj], ov.as_overrides(stored), lambda _l: key)
    baked = obj.to_dict()

    lensed = build_link().to_dict()
    ov.relens([lensed], stored, IDENTITY)

    for field in ("tier", "score", "confidence", "tierReason", "userAsserted"):
        check(f"{field} agrees", baked.get(field) == lensed.get(field),
              f"object={baked.get(field)!r} dict={lensed.get(field)!r}")
    check("machine verdict is preserved identically",
          baked.get("machine") == lensed.get("machine"),
          f"object={baked.get('machine')!r} dict={lensed.get('machine')!r}")


print("\nre-lensing a snapshot that already baked the verdict in")
key, stored = stored_with("confirm")
obj = build_link()
score_mod.apply_overrides([obj], ov.as_overrides(stored), lambda _l: key)
evidence = {"lnk1": [e.to_dict() for e in obj.evidence]}
baked = [obj.to_dict()]
before = copy.deepcopy(baked)

ov.relens(baked, stored, IDENTITY, evidence)
check("same verdict re-applied is a no-op", baked == before,
      f"\n       before={before[0]}\n       after ={baked[0]}")
check("the evidence row is not duplicated",
      sum(1 for e in evidence["lnk1"] if e["source"].startswith("override.user.")) == 1,
      f"{[e['source'] for e in evidence['lnk1']]}")

ov.relens(baked, stored, IDENTITY, evidence)
check("relensing twice more is still a no-op", baked == before)


print("\nchanging a verdict after the snapshot was taken")
_, rejected = stored_with("reject")
ov.relens(baked, rejected, IDENTITY, evidence)
check("tier follows the new verdict", baked[0]["tier"] == "rejected", baked[0]["tier"])
check("score is measured from the ENGINE's score, not the old verdict's",
      baked[0]["score"] == round(1.2 - 99.0, 4), baked[0]["score"])
check("exactly one override row remains",
      sum(1 for e in evidence["lnk1"] if e["source"].startswith("override.user.")) == 1)


print("\nclearing a verdict restores exactly what the engine concluded")
ov.relens(baked, {"links": {}, "wan": {}}, IDENTITY, evidence)
clean = build_link().to_dict()
for field in ("tier", "score", "confidence", "tierReason", "userAsserted"):
    check(f"{field} is back to the engine's value",
          baked[0].get(field) == clean.get(field),
          f"got={baked[0].get(field)!r} want={clean.get(field)!r}")
check("no machine block is left behind", "machine" not in baked[0])
check("no override block is left behind", "override" not in baked[0])
check("no override evidence is left behind",
      not any(e["source"].startswith("override.user.") for e in evidence["lnk1"]))
check("the engine's own evidence survived untouched", len(evidence["lnk1"]) == 2)


print("\nthe /graph path, which ships summaries and no evidence")
key, stored = stored_with("confirm")
graph = [build_link().to_dict()]
ov.relens(graph, stored, IDENTITY)
once = copy.deepcopy(graph)
check("the verdict is applied", graph[0]["tier"] == "confirmed")
check("the summary counts the verdict once",
      graph[0]["evidenceSummary"]["total"] == 3, graph[0]["evidenceSummary"])
ov.relens(graph, stored, IDENTITY)
ov.relens(graph, stored, IDENTITY)
check("re-lensing without evidence is idempotent too", graph == once,
      f"\n       once={once[0]['evidenceSummary']}\n       thrice={graph[0]['evidenceSummary']}")
ov.relens(graph, {"links": {}, "wan": {}}, IDENTITY)
check("and clearing restores the engine's summary",
      graph[0]["evidenceSummary"] == build_link().to_dict()["evidenceSummary"],
      f"{graph[0]['evidenceSummary']}")


print("\na verdict on a link that is not in this snapshot")
_, elsewhere = stored_with("confirm")
elsewhere["links"]["zz#1|yy#2"] = {"key": "zz#1|yy#2", "verdict": "confirm",
                                   "by": "x", "at": "", "note": ""}
untouched = [build_link().to_dict()]
ov.relens(untouched, {"links": {"zz#1|yy#2": elsewhere["links"]["zz#1|yy#2"]}, "wan": {}},
          IDENTITY)
check("an unmatched verdict changes nothing", untouched == [build_link().to_dict()])


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
