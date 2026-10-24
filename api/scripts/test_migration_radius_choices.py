#!/usr/bin/env python3
"""
SZ -> R1 migration: RADIUS choices on Review.

R1 RADIUS profiles are tenant-wide; a migration is one zone into one venue.
Before this, every RADIUS service a zone referenced was found-or-created by
name with a placeholder secret, with no say in it. Now each one is a choice --
create, link an existing R1 profile, or don't migrate -- and a WLAN whose
service is not migrated is either skipped or converted to R1 DPSK.

WHAT THIS GUARDS

  1. Only services the zone's WLANs reference are offered, with who uses them.
  2. Nothing offered to the UI carries a shared secret (SZ or R1).
  3. Suggestions: same server -> existing; same name -> existing; else create.
  4. The plan request refuses inconsistent choices, and its secret never
     appears in a repr or a JSON dump.
  5. Validate: a skipped service without a per-WLAN fallback BLOCKS the plan;
     "skip" drops the WLAN; "dpsk" converts it; a missing R1 profile blocks.
  6. Validate: no secret -> a placeholder warning on Review.
  7. Phase: create uses the typed secret, existing links without creating
     (and is not tracked as created), skip creates nothing, a same-named R1
     profile is linked rather than duplicated, and the secret key is deleted.
  8. Phase: a plan with no choices (older client) still creates, as before.
  9. Verbose R1 logging masks secrets and passphrases.

Usage:
    docker compose exec backend python scripts/test_migration_radius_choices.py

Exits non-zero on failure.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import ValidationError

from schemas.sz_migration import (
    SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZReferencedObject, SZWLANGroup, SZWLANGroupMember,
)
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.radius_choices import (
    RADIUS_SECRET_KEY, r1_profile_summary, referenced_services, suggest,
)
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.validate as validate_mod
import workflow.phases.sz_migration.create_radius as radius_mod
from routers.sz_migration.router import RadiusDecision
from r1api.client import _mask_for_log, _mask_text_for_log

SECRET = "typed-on-review-s3cret"
R1_SECRET = "already-in-r1-secret"
SZ_LEAK = "sz-should-never-have-this"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


# ==================== World ====================

def broadcast_everywhere(snap):
    """One AP broadcasting every WLAN via the zone's default WLAN Group, so
    each WLAN is placed (WLANs no AP broadcasts aren't migrated)."""
    snap.wlan_groups = [SZWLANGroup(id="wg", name="default", members=[
        SZWLANGroupMember(id=w.id, name=w.name, ssid=w.ssid) for w in snap.wlans])]
    snap.aps = [{"apMac": "aa:bb", "deviceName": "AP-1"}]
    return snap


def _snapshot():
    def ref(sid, name, ip, ref_type="auth_service"):
        return f"{ref_type}:{sid}", SZReferencedObject(
            ref_type=ref_type, id=sid, name=name,
            raw={"name": name, "primary": {"ip": ip, "port": 1812, "sharedSecret": SZ_LEAK}},
        )
    wlans = [
        SZWLANFull(id="w-corp", name="Corp", ssid="Corp", auth_type="WPA2-Enterprise",
                   auth_service_id="s-cp", accounting_service_id="s-acct"),
        SZWLANFull(id="w-staff", name="Staff", ssid="Staff", auth_type="WPA2-Enterprise",
                   auth_service_id="s-cp"),
        SZWLANFull(id="w-lab", name="Lab", ssid="Lab", auth_type="WPA2-Enterprise",
                   auth_service_id="s-lab"),
        SZWLANFull(id="w-guest", name="Guest", ssid="Guest", auth_type="WPA2-PSK"),
    ]
    refs = dict([
        ref("s-cp", "cloudpath", "10.0.0.5"),
        ref("s-acct", "acct", "10.0.0.6", "accounting_service"),
        ref("s-lab", "lab-radius", "10.9.9.9"),
        # Referenced by NO WLAN in this zone: must never be offered.
        ref("s-other", "other-zone", "10.7.7.7"),
    ])
    return SZMigrationSnapshot(zone=SZZoneSnapshot(id="z", name="Zone"), wlans=wlans,
                               referenced_objects=refs)


def snapshot():
    return broadcast_everywhere(_snapshot())


R1_PROFILES = [
    {"id": "rp-cp", "name": "Cloudpath-Prod", "type": "AUTHENTICATION",
     "primary": {"ip": "10.0.0.5", "port": 1812, "sharedSecret": R1_SECRET}},
    {"id": "rp-lab-name", "name": "lab-radius", "type": "AUTHENTICATION",
     "primary": {"ip": "10.1.1.1", "port": 1812, "sharedSecret": R1_SECRET}},
]


class FakeRedis:
    def __init__(self, snap):
        self.kv = {"sz_migration:extraction:snap:snapshot": snap.model_dump_json()}
        self.hashes = {}

    async def get(self, k):
        return self.kv.get(k)

    async def hget(self, k, f):
        return self.hashes.get(k, {}).get(f)

    async def delete(self, k):
        self.hashes.pop(k, None)
        self.kv.pop(k, None)


class FakeRadius:
    def __init__(self, profiles):
        self.profiles = list(profiles)
        self.created = []

    def get_radius_profiles(self, tenant_id=None):
        return self.profiles

    def find_radius_profile_by_name(self, tenant_id, name):
        return next((p for p in self.profiles if p["name"] == name), None)

    async def create_radius_profile(self, **kw):
        self.created.append(kw)
        pid = f"rp-new-{len(self.created)}"
        self.profiles.append({"id": pid, "name": kw["name"], "type": kw["profile_type"]})
        return {"id": pid}


class FakeR1:
    def __init__(self, profiles=R1_PROFILES):
        self.radius_profiles = FakeRadius(profiles)


class FakeState:
    def __init__(self):
        self.tracked = []

    async def track_created_resource(self, job_id, rtype, data):
        self.tracked.append((rtype, data))


def ctx(r1, options, state=None):
    return PhaseContext(job_id="job-1", r1_client=r1, venue_id="v", tenant_id="t",
                        state_manager=state, options=options)


async def run_validate(snap, options, r1=None):
    redis = FakeRedis(snap)

    async def fake_redis():
        return redis

    async def fake_inventory(*a, **k):
        return R1VenueInventory(venue_id="v", venue_name="V", tenant_id="t")

    validate_mod.get_redis_client = fake_redis
    validate_mod.capture_venue_inventory = fake_inventory
    phase = validate_mod.ValidateMigrationPhase(ctx(r1 or FakeR1(), options))
    return await phase.execute(phase.Inputs(sz_snapshot_job_id="snap"))


async def run_radius(snap, options, secrets=None, r1=None):
    redis = FakeRedis(snap)
    if secrets:
        redis.hashes[RADIUS_SECRET_KEY.format(job_id="job-1")] = dict(secrets)

    async def fake_redis():
        return redis

    radius_mod.get_redis_client = fake_redis
    r1 = r1 or FakeR1()
    state = FakeState()
    phase = radius_mod.CreateRadiusProfilesPhase(ctx(r1, options, state))
    out = await phase.execute(phase.Inputs(sz_snapshot_job_id="snap"))
    return out, r1, state, redis


def errors(out):
    return [c.description for c in out.validation_result.conflicts if c.severity == "error"]


def warnings(out):
    return [c.description for c in out.validation_result.conflicts if c.severity == "warning"]


# ==================== Tests ====================

async def main():
    failures = 0
    snap = snapshot()
    print("Migration RADIUS choices")

    # 1 + 2. what is offered
    services = referenced_services(snap)
    ids = {s["id"] for s in services}
    failures += check("only zone-referenced services are offered", ids == {"s-cp", "s-acct", "s-lab"}, str(ids))
    cp = next(s for s in services if s["id"] == "s-cp")
    failures += check("a service lists every WLAN using it",
                      {u["wlan_name"] for u in cp["used_by"]} == {"Corp", "Staff"})
    profiles = [r1_profile_summary(p) for p in R1_PROFILES]
    offered = json.dumps({"services": services, "r1_profiles": profiles})
    failures += check("no shared secret in what the UI is sent",
                      SZ_LEAK not in offered and R1_SECRET not in offered)

    # 3. suggestions
    failures += check("same server -> existing",
                      suggest(cp, profiles) == {"action": "existing", "r1_profile_id": "rp-cp",
                                                "reason": suggest(cp, profiles)["reason"]})
    lab = next(s for s in services if s["id"] == "s-lab")
    failures += check("same name -> existing", suggest(lab, profiles)["r1_profile_id"] == "rp-lab-name")
    acct = next(s for s in services if s["id"] == "s-acct")
    failures += check("accounting-only service with no match -> create",
                      suggest(acct, profiles)["action"] == "create")

    # 4. request model
    for bad, label in (({"action": "existing"}, "existing without a profile is refused"),
                       ({"action": "skip", "shared_secret": "x"}, "a secret on 'skip' is refused")):
        try:
            RadiusDecision(**bad)
            ok = False
        except ValidationError:
            ok = True
        failures += check(label, ok)
    d = RadiusDecision(action="create", shared_secret=SECRET)
    failures += check("the secret never shows in repr or JSON",
                      SECRET not in repr(d) and SECRET not in d.model_dump_json())

    # 5 + 6. validate
    base = {"s-cp": {"action": "existing", "r1_profile_id": "rp-cp"},
            "s-acct": {"action": "create", "secret_provided": False},
            "s-lab": {"action": "skip"}}
    out = await run_validate(snap, {"radius_decisions": base})
    failures += check("skipped service without a fallback blocks the plan",
                      not out.validation_result.valid and any("Lab" in e for e in errors(out)),
                      str(errors(out)))

    out = await run_validate(snap, {"radius_decisions": base, "wlan_fallbacks": {"w-lab": "skip"}})
    failures += check("fallback 'skip' -> plan valid, WLAN has no unit",
                      out.validation_result.valid and "w-lab" not in out.unit_mappings,
                      str(errors(out)))
    failures += check("skipped WLAN is shown as skipped on Review",
                      out.type_mappings["w-lab"]["r1_network_type"] == "skipped")
    failures += check("no secret -> placeholder warning",
                      any("placeholder" in w for w in warnings(out)), str(warnings(out)))
    acts = {(a.resource_type, a.name): a.action for a in out.validation_result.actions}
    failures += check("existing profile listed as reuse",
                      acts.get(("radius_profile", "Cloudpath-Prod")) == "reuse", str(acts))

    out = await run_validate(snap, {"radius_decisions": base, "wlan_fallbacks": {"w-lab": "dpsk"}})
    lab_unit = out.unit_mappings.get("w-lab")
    failures += check("fallback 'dpsk' converts the WLAN",
                      lab_unit is not None and lab_unit.plan.extra["r1_network_type"] == "dpsk"
                      and out.type_mappings["w-lab"]["r1_network_type"] == "dpsk")

    gone = dict(base, **{"s-cp": {"action": "existing", "r1_profile_id": "rp-deleted"}})
    out = await run_validate(snap, {"radius_decisions": gone, "wlan_fallbacks": {"w-lab": "skip"}})
    failures += check("chosen R1 profile that no longer exists blocks",
                      not out.validation_result.valid
                      and any("no longer exists" in e for e in errors(out)))

    # The failed run's leftover: same name + server as SZ, placeholder secret.
    leftover = [{"id": "rp-left", "name": "cloudpath", "type": "AUTHENTICATION",
                 "primary": {"ip": "10.0.0.5", "port": 1812,
                             "sharedSecret": radius_mod.PLACEHOLDER_RADIUS_SECRET}}]
    lsum = [r1_profile_summary(p) for p in leftover]
    failures += check("placeholder profile is flagged, secret not exposed",
                      lsum[0]["placeholder_secret"] is True
                      and radius_mod.PLACEHOLDER_RADIUS_SECRET not in json.dumps(lsum))
    sug = suggest(cp, lsum)
    failures += check("a placeholder profile is never suggested as 'same server'",
                      "same server" not in sug["reason"] and "placeholder" in sug["reason"], sug["reason"])
    out = await run_validate(snap, {"radius_decisions": {
        "s-cp": {"action": "create", "secret_provided": True},
        "s-acct": {"action": "skip"}, "s-lab": {"action": "skip"}},
        "wlan_fallbacks": {"w-lab": "skip"}}, r1=FakeR1(profiles=leftover))
    failures += check("Create colliding with an existing name warns the secret won't apply",
                      any("NOT applied" in w and "placeholder" in w for w in warnings(out)),
                      str(warnings(out)))
    out = await run_validate(snap, {"radius_decisions": {
        "s-cp": {"action": "existing", "r1_profile_id": "rp-left"},
        "s-acct": {"action": "skip"}, "s-lab": {"action": "skip"}},
        "wlan_fallbacks": {"w-lab": "skip"}}, r1=FakeR1(profiles=leftover))
    failures += check("linking a placeholder profile warns",
                      any("placeholder" in w for w in warnings(out)), str(warnings(out)))

    # 7. phase
    decisions = {"s-cp": {"action": "existing", "r1_profile_id": "rp-cp"},
                 "s-acct": {"action": "create", "secret_provided": True},
                 "s-lab": {"action": "skip"}}
    out, r1, state, redis = await run_radius(snap, {"radius_decisions": decisions},
                                             secrets={"s-acct": SECRET})
    created = r1.radius_profiles.created
    failures += check("create uses the typed secret",
                      len(created) == 1 and created[0]["primary_secret"] == SECRET
                      and created[0]["profile_type"] == "ACCOUNTING")
    failures += check("existing is linked, not created",
                      out.radius_profile_mappings.get("s-cp") == "rp-cp")
    failures += check("skip creates nothing and maps nothing", "s-lab" not in out.radius_profile_mappings)
    failures += check("only created profiles are tracked as created",
                      [d["name"] for _, d in state.tracked] == ["acct"], str(state.tracked))
    failures += check("secret key deleted after the phase",
                      RADIUS_SECRET_KEY.format(job_id="job-1") not in redis.hashes)
    failures += check("counts are right (1 created, 1 linked)",
                      out.radius_profiles_created == 1 and out.radius_profiles_reused == 1)

    out, r1, *_ = await run_radius(snap, {"radius_decisions": {
        "s-cp": {"action": "skip"}, "s-acct": {"action": "skip"},
        "s-lab": {"action": "create", "secret_provided": True}}}, secrets={"s-lab": SECRET})
    failures += check("same-named R1 profile is linked, not duplicated",
                      not r1.radius_profiles.created
                      and out.radius_profile_mappings.get("s-lab") == "rp-lab-name")

    out, r1, *_ = await run_radius(snap, {"radius_decisions": {
        "s-cp": {"action": "skip"}, "s-lab": {"action": "skip"},
        "s-acct": {"action": "create", "secret_provided": False}}})
    failures += check("no secret -> placeholder",
                      r1.radius_profiles.created[0]["primary_secret"]
                      == radius_mod.PLACEHOLDER_RADIUS_SECRET)

    # 8. legacy
    out, r1, *_ = await run_radius(snap, {}, r1=FakeR1(profiles=[]))
    failures += check("no choices (older plan) -> creates every referenced service",
                      len(r1.radius_profiles.created) == 3, str(len(r1.radius_profiles.created)))

    # 9. verbose logging
    masked = json.dumps(_mask_for_log({"primary": {"sharedSecret": SECRET},
                                       "passphrase": SECRET, "passphraseFormat": "MOST_SECURED"}))
    failures += check("payload masking hides secrets, keeps *Format",
                      SECRET not in masked and "MOST_SECURED" in masked)
    text = _mask_text_for_log('{"data":[{"passphrase":"%s","sharedSecret":"%s","name":"x"}]}' % (SECRET, SECRET))
    failures += check("response-body masking hides secrets", SECRET not in text and '"name":"x"' in text)

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
