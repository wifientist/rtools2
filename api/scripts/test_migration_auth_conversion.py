#!/usr/bin/env python3
"""
SZ -> R1 migration: external DPSK classification and conversion to R1 DPSK.

Every "WPA2-Enterprise" WLAN in the real zones was SZ external DPSK (a WPA2
Personal WLAN whose keys Cloudpath checks) or an encrypted WISPr hotspot:
the classifier read "has an auth service" as 802.1X. Migrated as AAA, the
clients -- who join with a passphrase -- would all be locked out.

WHAT THIS GUARDS

  1. Classification: externalDpsk.enabled -> "External DPSK"; 802.1X only
     from the WLAN type (Standard_8021X*, Hotspot20); encrypted portal WLANs
     -> "WPA2-PSK + Portal"; rows with no type keep the old rule.
  2. Stored snapshots re-classify on load (old "WPA2-Enterprise" labels).
  3. Mapping: External DPSK -> dpsk_external (kept external by default);
     portal PSK -> psk.
  4. Validate, kept external: dpsk_external, its RADIUS service needs a
     choice; a portal WLAN created as plain PSK needs none for its service. Converted: dpsk, the service is "not needed" (no choice, not
     created). Only RADIUS-backed WLANs convert (a PSK id is ignored).
  5. Network: kept external = DPSK network with useDpskService false and the
     RADIUS profile linked; converted = DPSK service network, no RADIUS link.
  6. create_radius skips a service with no decision (all users converted).
  8. VLANs for Review: R1 gets the SZ access VLAN (1 when none) on create;
     a reused same-named R1 network keeps its own; pools/AAA override noted.
  7. Audit: External DPSK is an external_dpsk backend; a kept-external DPSK
     network is an explained type change. WLAN Grabber still offers dpsk.

Usage:
    docker compose exec backend python scripts/test_migration_auth_conversion.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import (
    SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZWLANGroup, SZWLANGroupMember, SZReferencedObject,
)
from schemas.r1_inventory import R1VenueInventory
from schemas.migration_audit import AuthPath, AuthPathAudit
from szapi.services.wlans import WlanService
from services.sz_migration.mapper import map_wlan_to_r1_type, wlan_vlans
from services.sz_migration.auditor import sz_auth_backend, type_change_explained_by_path
from services.wlan_grabber import MODES
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.validate as validate_mod
import workflow.phases.sz_migration.create_networks as networks_mod
import workflow.phases.sz_migration.create_radius as radius_mod

CP = {"id": "svc-cp", "name": "cloudpath"}


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def raw(name, **kw):
    base = {"id": name, "name": name, "ssid": name, "type": "Standard_Open",
            "encryption": {"method": "WPA2", "passphrase": "x" * 12}}
    base.update(kw)
    return base


EXT_DPSK = raw("101@Site", encryption={"method": "WPA2"}, authServiceOrProfile=CP,
               externalDpsk={"enabled": True, "authService": CP})
DOT1X = raw("Corp", type="Standard_8021X", encryption={"method": "WPA2"}, authServiceOrProfile=CP)
HOTSPOT = raw("Guest", type="Hotspot", authServiceOrProfile={"id": "r2", "name": "radius2"},
              portalServiceProfile={"id": "p", "name": "Guest"})
PSK = raw("Staff")


def wlan(r, label="WPA2-Enterprise"):
    """As stored by an extraction made before the fix: the old label."""
    return SZWLANFull(id=r["id"], name=r["name"], ssid=r["ssid"], auth_type=label, raw=r,
                      encryption=r.get("encryption"), external_dpsk=r.get("externalDpsk"),
                      auth_service_id=(r.get("authServiceOrProfile") or {}).get("id"))


def snapshot():
    wlans = [wlan(EXT_DPSK), wlan(DOT1X), wlan(PSK, "WPA2-PSK"), wlan(HOTSPOT)]
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone"),
        wlans=wlans,
        wlan_groups=[SZWLANGroup(id="wg", name="default", members=[
            SZWLANGroupMember(id=w.id, name=w.name, ssid=w.ssid) for w in wlans])],
        aps=[{"apMac": "aa", "deviceName": "AP-1"}],
        referenced_objects={"auth_service:svc-cp": SZReferencedObject(
            ref_type="auth_service", id="svc-cp", name="cloudpath",
            raw={"name": "cloudpath", "primary": {"ip": "10.0.0.5", "port": 1812}})},
    )


async def run_validate(options):
    snap = snapshot()

    class R:
        async def get(self, k):
            return snap.model_dump_json()

    async def fake_redis():
        return R()

    async def fake_inventory(*a, **k):
        return R1VenueInventory(venue_id="v", venue_name="V", tenant_id="t",
                                ap_groups=[{"id": "d", "name": "", "isDefault": True}])

    validate_mod.get_redis_client = fake_redis
    validate_mod.capture_venue_inventory = fake_inventory
    r1 = SimpleNamespace(radius_profiles=SimpleNamespace(get_radius_profiles=lambda t=None: []))
    phase = validate_mod.ValidateMigrationPhase(PhaseContext(
        job_id="j", r1_client=r1, venue_id="v", tenant_id="t", options=options))
    return await phase.execute(phase.Inputs(sz_snapshot_job_id="snap"))


async def run_create_network(r1_type):
    calls = []
    extra = {"sz_wlan_name": "101@Site", "ssid": "101@Site", "r1_network_type": r1_type, "vlan_id": 10,
             "auth_service_id": "svc-cp"}
    unit = SimpleNamespace(plan=SimpleNamespace(extra=extra))
    job = SimpleNamespace(global_phase_results={
        "create_radius_profiles": {"radius_profile_mappings": {"svc-cp": "r1-cp"}}})

    class State:
        async def get_unit(self, *a):
            return unit

        async def get_job(self, *a):
            return job

    async def find(*a, **k):
        return None

    async def create_dpsk(**kw):
        calls.append(("create_dpsk", kw))
        return {"id": "net-1"}

    async def link(**kw):
        calls.append(("link_radius", kw))

    r1 = SimpleNamespace(
        networks=SimpleNamespace(find_wifi_network_by_name=find, create_dpsk_wifi_network=create_dpsk),
        radius_profiles=SimpleNamespace(link_radius_to_network=link))
    phase = networks_mod.CreateNetworksPhase(PhaseContext(
        job_id="j", r1_client=r1, venue_id="v", tenant_id="t", state_manager=State(), options={}))

    async def noop(*a, **k):
        pass
    phase.track_resource = noop
    await phase.execute(phase.Inputs(unit_id="w", unit_number="101@Site"))
    return calls


async def main():
    failures = 0

    print("\n1. Classification")
    c = WlanService.extract_auth_type
    failures += check("external DPSK", c(EXT_DPSK) == "External DPSK", c(EXT_DPSK))
    failures += check("802.1X by type", c(DOT1X) == "WPA2-Enterprise", c(DOT1X))
    failures += check("encrypted hotspot with an auth service is not Enterprise",
                      c(HOTSPOT) == "WPA2-PSK + Portal", c(HOTSPOT))
    failures += check("Standard_Open WPA2 + auth service (no ext DPSK) is not Enterprise",
                      c(raw("X", authServiceOrProfile=CP)) == "WPA2-PSK")
    no_type = {k: v for k, v in raw("Y", authServiceOrProfile=CP).items() if k != "type"}
    failures += check("no type: old rule (auth service = Enterprise)", c(no_type) == "WPA2-Enterprise")
    failures += check("Hotspot 2.0 is 802.1X",
                      c(raw("H", type="Hotspot20", authServiceOrProfile=CP)) == "WPA2-Enterprise")

    print("\n2. Stored snapshots re-classify on load")
    old = SZMigrationSnapshot.model_validate_json(snapshot().model_dump_json())
    failures += check("old 'WPA2-Enterprise' label replaced",
                      [w.auth_type for w in old.wlans] == ["External DPSK", "WPA2-Enterprise", "WPA2-PSK",
                                                            "WPA2-PSK + Portal"],
                      str([w.auth_type for w in old.wlans]))

    print("\n3. Mapping")
    m = map_wlan_to_r1_type(wlan(EXT_DPSK))
    failures += check("External DPSK -> dpsk_external, external, no forced decision",
                      m.r1_network_type == "dpsk_external" and m.dpsk_type == "external"
                      and not m.needs_user_decision)
    failures += check("portal PSK -> psk with a note",
                      map_wlan_to_r1_type(wlan(HOTSPOT)).r1_network_type == "psk")

    print("\n4. Validate")
    out = await run_validate({"radius_decisions": {}})
    ex = out.unit_mappings["101@Site"].plan.extra
    failures += check("kept external by default", ex["r1_network_type"] == "dpsk_external")
    errs = [c.description for c in out.validation_result.conflicts if c.severity == "error"]
    failures += check("its RADIUS service needs a choice", any("cloudpath" in e for e in errs), str(errs))

    out = await run_validate({"radius_decisions": {}, "auth_conversions": {
        "101@Site": "dpsk", "Corp": "dpsk", "Staff": "dpsk"}})
    types = {k: u.plan.extra["r1_network_type"] for k, u in out.unit_mappings.items()}
    failures += check("external DPSK and Enterprise converted to dpsk, PSK untouched",
                      types == {"101@Site": "dpsk", "Corp": "dpsk", "Staff": "psk", "Guest": "psk"}, str(types))
    errs = [c.description for c in out.validation_result.conflicts if c.severity == "error"]
    failures += check("no RADIUS choice needed once all its WLANs convert", not errs, str(errs))
    radius = [a for a in out.validation_result.actions if a.resource_type == "radius_profile"]
    failures += check("services reported as not needed (Cloudpath; the portal's radius2)",
                      len(radius) == 2 and "cloudpath" in [a.name for a in radius]
                      and all(a.action == "skip" and "Not needed" in a.notes[0] for a in radius), str(radius))
    failures += check("converted flagged for Review", out.type_mappings["Corp"].get("converted") is True)

    out = await run_validate({"radius_decisions": {}, "auth_conversions": {"101@Site": "dpsk"}})
    errs = [c.description for c in out.validation_result.conflicts if c.severity == "error"]
    failures += check("service still needed while one user stays external",
                      any("cloudpath" in e and "Corp" in e for e in errs), str(errs))

    print("\n5. Network creation")
    calls = await run_create_network("dpsk_external")
    create = next(kw for n, kw in calls if n == "create_dpsk")
    failures += check("kept external: DPSK network without DPSK service", create["use_dpsk_service"] is False)
    failures += check("kept external: RADIUS profile linked",
                      any(n == "link_radius" and kw["radius_profile_id"] == "r1-cp" for n, kw in calls))
    calls = await run_create_network("dpsk")
    create = next(kw for n, kw in calls if n == "create_dpsk")
    failures += check("converted: DPSK service network, no RADIUS link",
                      create["use_dpsk_service"] is True and not any(n == "link_radius" for n, _ in calls))

    print("\n6. create_radius")
    snap = snapshot()

    class R:
        async def get(self, k):
            return snap.model_dump_json()

        async def hgetall(self, k):
            return {}

        async def delete(self, k):
            return 0

    async def fake_redis():
        return R()
    radius_mod.get_redis_client = fake_redis
    created = []
    r1 = SimpleNamespace(radius_profiles=SimpleNamespace(
        get_radius_profiles=lambda t=None: [],
        create_radius_profile=lambda *a, **k: created.append(k)))
    phase = radius_mod.CreateRadiusProfilesPhase(PhaseContext(
        job_id="j", r1_client=r1, venue_id="v", tenant_id="t", options={"radius_decisions": {}}))

    async def noop(*a, **k):
        pass
    phase.emit = noop
    out = await phase.execute(phase.Inputs(sz_snapshot_job_id="snap"))
    failures += check("service without a decision is not created", not created and out.radius_profile_mappings == {})

    print("\n7. Audit and WLAN Grabber")
    ext = wlan(EXT_DPSK)
    ext = SZWLANFull.model_validate(ext.model_dump())
    failures += check("External DPSK -> external_dpsk backend",
                      sz_auth_backend(ext, map_wlan_to_r1_type(ext)) == "external_dpsk")
    a = AuthPathAudit(sz_wlan_id="w", sz_wlan_name="w", sz_ssid="w", sz_auth_type="External DPSK",
                      sz_backend="external_dpsk", r1_network_type="dpsk", path=AuthPath.EXTERNAL, status="ok")
    failures += check("kept-external DPSK network is an explained type change",
                      type_change_explained_by_path("external_dpsk", a))
    failures += check("Grabber still creates external DPSK as R1 DPSK", MODES["dpsk_external"] == ("dpsk",))

    print("\n8. VLANs")
    w = wlan(raw("V", vlan={"accessVlan": 20, "vlanPooling": {"id": "p", "name": "pool-a"}, "aaaVlanOverride": True}))
    w = SZWLANFull.model_validate({**w.model_dump(), "vlan_id": 20})
    v = wlan_vlans(w)
    failures += check("create: SZ access VLAN carried", v["sz_vlan"] == 20 and v["r1_vlan"] == 20
                      and v["r1_vlan_source"] == "create")
    failures += check("pool and AAA override noted",
                      len(v["vlan_notes"]) == 2 and "pool-a" in v["vlan_notes"][0])
    v = wlan_vlans(w, {"name": "V", "vlan": 30})
    failures += check("reused R1 network keeps its own VLAN", v["r1_vlan"] == 30 and v["r1_vlan_source"] == "existing")
    failures += check("no SZ VLAN -> 1", wlan_vlans(wlan(PSK, "WPA2-PSK"))["r1_vlan"] == 1)

    print(f"\n{'FAILED' if failures else 'All checks passed'}{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
