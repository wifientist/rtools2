#!/usr/bin/env python3
"""
Migration Audit: captive portal WLANs setting by setting, WLAN Groups in
the summary, portal WLANs first.

WHAT THIS GUARDS
  1. A hotspot WLAN is compared against its R1 WISPr network on every
     setting with a counterpart: type, URLs, redirect (SZ "original URL" vs
     an R1 URL is a difference -- Solis Dresden), walled garden (missing /
     extra entries named), sessions, auth server by ADDRESS (R1 renames the
     profile "radius2_24179"), proxying, bypass CNA, MAC bypass, VLAN,
     isolation.
  2. Everything else either portal holds is listed per side (sz_only /
     r1_only), so nothing goes unseen.
  3. Signing / integration key: never a value -- not in checks, not in the
     per-side lists, not anywhere in the portal output. Compared as keyed
     fingerprints when both sides are readable (match / differs, also from
     a cached capture); SZ's API returns it blank, so then it's "unknown"
     and points at the key check. The key check (user-entered key vs live
     R1) answers match / differs only, and both R1 copies must match. The
     capture cache fingerprints the key, including in captures written raw
     before the key joined the credential list.
  4. A portal profile SZ refused to return (the pre-24-Oct 403) says to
     refresh; a portal WLAN with no R1 network is "missing"; an R1 guest
     network no SZ portal pairs with is listed.
  5. Portal WLANs come first in the WLAN lists, then by name.
  6. The summary's WLAN Groups row: SZ WLAN Group members carry no SSID, so
     the "carried by an R1 AP Group" check reads it from the WLAN (it used
     to find nothing, everywhere).

Usage:
    docker compose exec backend python scripts/test_audit_portal.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import (
    SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZReferencedObject, SZWLANGroup, SZWLANGroupMember,
    SZAPGroupEnriched,
)
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.auditor import run_audit
from services.sz_migration.audit_portal import check_portal_key
from services.sz_migration.audit_cache import strip_credentials
from services.sz_migration.audit_redaction import CREDENTIAL_PRESENT
from services.sz_migration.passphrase_check import fingerprint

KEY = "SigningKey-Do-Not-Leak-8842"
PORTAL_URL = "http://captive-portal.example/captive_guest.html"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def snapshot(portal_raw=None):
    guest_raw = {"id": "g", "name": "Site Guest", "ssid": "Site Guest", "type": "Hotspot", "bypassCNA": True,
                 "portalServiceProfile": {"id": "hp1", "name": "guest"},
                 "authServiceOrProfile": {"id": "rs", "name": "radius2", "throughController": True},
                 "vlan": {"accessVlan": 1004}, "advancedOptions": {"clientIsolationEnabled": True},
                 "encryption": {"method": "None"}}
    portal = portal_raw if portal_raw is not None else {
        "id": "hp1", "name": "guest", "portalType": "External", "portalUrl": PORTAL_URL,
        "backupPortalUrl": "", "redirect": {}, "walledGardens": ["*.xfinity.com", "*.comcast.net", "*.old.net"],
        "userSession": {"timeoutInMin": 1440, "gracePeriodInMin": 60}, "httpsRedirect": True,
        "macAddressFormat": 4, "smartClientSupport": "Enabled", "signatureSigningKey": KEY, "zoneId": "z"}
    wlans = [
        SZWLANFull(id="u2", name="Zeta Unit", ssid="Zeta Unit", auth_type="WPA2-PSK", raw={"type": "Standard_Open"}),
        SZWLANFull(id="u1", name="Alpha Unit", ssid="Alpha Unit", auth_type="WPA2-PSK", raw={"type": "Standard_Open"}),
        SZWLANFull(id="g", name="Site Guest", ssid="Site Guest", auth_type="Open + Portal",
                   auth_service_id="rs", raw=guest_raw),
        SZWLANFull(id="g2", name="Lobby Portal", ssid="Lobby Portal", auth_type="Open + Portal",
                   raw={"type": "Hotspot", "portalServiceProfile": {"id": "hp2", "name": "lobby"}}),
    ]
    m = {w.id: SZWLANGroupMember(id=w.id, name=w.name) for w in wlans}   # no ssid: as SZ returns them
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Zone"), wlans=wlans,
        wlan_groups=[SZWLANGroup(id="wg-default", name="default", members=[m["g"], m["u1"]]),
                     SZWLANGroup(id="wg-a", name="A", members=[m["g"], m["u1"], m["u2"]])],
        ap_groups=[SZAPGroupEnriched(id="apg", name="default", ap_count=0)],
        referenced_objects={
            "portal_service_profile:hp1": SZReferencedObject(ref_type="portal_service_profile", id="hp1",
                                                             name="guest", raw=portal),
            "portal_service_profile:hp2": SZReferencedObject(
                ref_type="portal_service_profile", id="hp2", name=None,
                raw={"_error": "SmartZone API error (403): Permission denied"}),
            "auth_service:rs": SZReferencedObject(ref_type="auth_service", id="rs", name="radius2",
                                                  raw={"name": "radius2", "primary": {"ip": "96.102.87.172", "port": 1812}}),
        },
    )


def r1(redirect=PORTAL_URL, integration_key=KEY):
    guest = {"id": "n-g", "name": "Site Guest", "type": "guest",
             "wlan": {"ssid": "Site Guest", "vlanId": 1004, "bypassCNA": True,
                      "bypassCPUsingMacAddressAuthentication": False,
                      "advancedCustomization": {"clientIsolation": True}},
             "guestPortal": {"guestNetworkType": "WISPr", "externalPortalUrl": PORTAL_URL, "redirectUrl": redirect,
                             "walledGardens": ["*.xfinity.com", "*.comcast.net", "*.new.net"],
                             "userSessionTimeout": 1440, "userSessionGracePeriod": 60, "maxDevices": 1,
                             "wisprPage": {"externalProviderName": "guest", "authType": "RADIUS",
                                           "captivePortalUrl": PORTAL_URL, "encryptMacIpEnabled": False,
                                           "integrationKey": integration_key}}}
    other = {"id": "n-x", "name": "Pool Cabana", "type": "guest",
             "wlan": {"ssid": "Pool Cabana"}, "guestPortal": {"guestNetworkType": "ClickThrough"}}
    units = [{"id": f"n-{i}", "name": n, "ssid": n, "nwSubType": "psk", "type": "psk"}
             for i, n in (("u1", "Alpha Unit"), ("u2", "Zeta Unit"))]
    rows = [{"id": "n-g", "name": "Site Guest", "ssid": "Site Guest", "nwSubType": "guest",
             "venueApGroups": [{"venueId": "v", "isAllApGroups": True}]},
            {"id": "n-x", "name": "Pool Cabana", "ssid": "Pool Cabana", "nwSubType": "guest"},
            {**units[0], "venueApGroups": [{"venueId": "v", "isAllApGroups": True}]},
            {**units[1], "venueApGroups": [{"venueId": "v", "isAllApGroups": False, "apGroupIds": ["r1-a"]}]}]
    inv = R1VenueInventory(venue_id="v", venue_name="V", tenant_id="t", wifi_networks=rows,
                           ap_groups=[{"id": "r1-def", "name": "", "isDefault": True}, {"id": "r1-a", "name": "A"}])
    facts = {"radius_by_network": {"n-g": [{"id": "rp", "name": "radius2_24179", "type": "AUTHENTICATION",
                                            "primary": "96.102.87.172:1812", "secondary": ""}]},
             "radius_settings": {"n-g": {"enable_auth_proxy": True, "enable_accounting_proxy": False}},
             "pools_by_network": {}, "errors": []}
    return inv, {"n-g": guest, "n-x": other, **{u["id"]: u for u in units}}, facts


def main():
    f = 0
    inv, details, facts = r1()
    rep = run_audit(snapshot(), inv, "s", "r", details, facts)
    p = next(x for x in rep.portals if x.sz_wlan_id == "g")
    by = {c.label: c for c in p.checks}

    print("\n1. Settings with an R1 counterpart")
    f += check("WISPr type matches", by["Portal type"].status == "match")
    f += check("portal URL + R1 WISPr page URL match", by["Portal URL"].status == "match"
               and by["Captive portal URL (R1 WISPr page)"].status == "match")
    f += check("SZ 'original URL' vs an R1 redirect URL: differs", by["Redirect after login"].status == "differs",
               str(by["Redirect after login"]))
    wg = by["Walled garden"]
    f += check("walled garden differences named", wg.status == "differs"
               and "*.old.net" in wg.note and "*.new.net" in wg.note, wg.note)
    f += check("sessions match", by["Session timeout (min)"].status == "match"
               and by["Grace period (min)"].status == "match")
    a = by["Authentication server"]
    f += check("auth server matched by address despite R1's renamed profile",
               a.status == "match" and "radius2_24179" in a.r1, str(a))
    f += check("proxy, bypass CNA, MAC bypass, VLAN, isolation compared",
               all(by[k].status == "match" for k in ("Authentication proxied through the controller",
                                                    "Bypass CNA (captive network assistant)",
                                                    "MAC auth bypasses the portal", "VLAN", "Client isolation")))
    f += check("overall: differs", p.status == "differs")

    print("\n2. Everything else, per side")
    sz_only = {c.label for c in p.sz_only}
    r1_only = {c.label for c in p.r1_only}
    f += check("SZ-only settings listed", {"httpsRedirect", "macAddressFormat", "smartClientSupport", "portalType"} <= sz_only,
               str(sz_only))
    f += check("R1-only settings listed", {"maxDevices"} <= r1_only, str(r1_only))
    f += check("compared fields aren't listed again", "portalUrl" not in sz_only and "externalPortalUrl" not in r1_only)

    print("\n3. Keys")
    KEYROW = "Signing key → integration key"

    def key_row(snap, details_):
        rp = run_audit(snap, inv, "s", "r", details_, facts)
        return {c.label: c for c in next(x for x in rp.portals if x.sz_wlan_id == "g").checks}[KEYROW], rp

    def with_key(k, ext=None):
        d = json.loads(json.dumps(details))
        d["n-g"]["guestPortal"]["wisprPage"]["integrationKey"] = k
        if ext is not None:
            d["n-g"]["guestPortal"]["externalPortalKey"] = ext
        return d

    k = by[KEYROW]
    f += check("same key both sides: match", k.status == "match" and k.sz == "set" and k.r1.startswith("set (key A)"), str(k))
    blob = json.dumps([x.model_dump() for x in rep.portals])
    f += check("the key's value appears nowhere in the portal output", KEY not in blob)
    k2, rep2 = key_row(snapshot(), with_key("OtherKey00000001"))
    f += check("different key in R1: differs", k2.status == "differs", str(k2))
    f += check("…and still no value in the output", "OtherKey00000001" not in json.dumps([x.model_dump() for x in rep2.portals]))
    k3, _ = key_row(snapshot(), with_key(None))
    f += check("SZ signs, R1 has no key: differs", k3.status == "differs" and k3.r1.startswith("not set"), str(k3))
    blank = snapshot()
    blank.referenced_objects["portal_service_profile:hp1"].raw["signatureSigningKey"] = ""
    k4, _ = key_row(blank, details)
    f += check("SZ blank: unknown, says SZ's API doesn't return it, points at the key check",
               k4.status == "unknown" and "doesn't return" in k4.sz and "Check a portal key" in k4.note, str(k4))
    k5, _ = key_row(snapshot(), with_key(CREDENTIAL_PRESENT))
    f += check("R1 capture from before fingerprints: unknown, refresh", k5.status == "unknown" and "refresh" in k5.note, str(k5))
    fp_snap = snapshot()
    fp_snap.referenced_objects["portal_service_profile:hp1"].raw["signatureSigningKey"] = fingerprint(KEY)
    k6, _ = key_row(fp_snap, details)
    f += check("cached SZ fingerprint vs live R1 key: match", k6.status == "match", str(k6))
    _, rep7 = key_row(snapshot(), with_key(KEY, ext="Mismatch0000000X"))
    rows7 = {c.label: c for c in next(x for x in rep7.portals if x.sz_wlan_id == "g").checks}
    f += check("R1's two key copies disagree: flagged", rows7.get("R1 portal key = integration key") is not None
               and rows7["R1 portal key = integration key"].status == "differs")

    print("\n3b. Key check (user-entered key vs live R1)")
    net = with_key(KEY, ext=KEY)["n-g"]
    r = check_portal_key(KEY, net)
    f += check("right key: match", r["status"] == "match", str(r))
    f += check("…notes MAC/IP encryption off", "encryption is off" in r["note"], r["note"])
    f += check("wrong key: differs", check_portal_key("Wrong", net)["status"] == "differs")
    half = with_key(KEY, ext="Mismatch0000000X")["n-g"]
    rh = check_portal_key(KEY, half)
    f += check("one copy wrong: differs, names it", rh["status"] == "differs" and "external portal key" in rh["note"], str(rh))
    f += check("no key in R1: not_set", check_portal_key(KEY, with_key(None)["n-g"])["status"] == "not_set")
    f += check("unreadable network: not_read", check_portal_key(KEY, None)["status"] == "not_read")
    f += check("no result carries a key", all(KEY not in json.dumps(check_portal_key(KEY, n))
                                               for n in (net, half, None)))

    print("\n3c. Capture cache")
    stripped = strip_credentials({"guestPortal": {"externalPortalKey": KEY, "wisprPage": {"integrationKey": KEY}},
                                  "signatureSigningKey": KEY, "sharedSecret": "s3cret", "x": CREDENTIAL_PRESENT})
    sb = json.dumps(stripped)
    f += check("portal keys become fingerprints, no raw value", KEY not in sb
               and stripped["signatureSigningKey"] == fingerprint(KEY)
               and stripped["guestPortal"]["wisprPage"]["integrationKey"] == fingerprint(KEY), sb)
    f += check("other secrets just marked present", stripped["sharedSecret"] == CREDENTIAL_PRESENT)
    f += check("stripping again changes nothing (old captures re-stripped on read)",
               strip_credentials(stripped) == stripped
               and strip_credentials({"integrationKey": CREDENTIAL_PRESENT}) == {"integrationKey": CREDENTIAL_PRESENT})

    print("\n4. Unreadable, unpaired")
    lob = next(x for x in rep.portals if x.sz_wlan_id == "g2")
    f += check("403'd portal profile: note says refresh", any("refresh the SZ side" in n for n in lob.notes), str(lob.notes))
    f += check("portal WLAN with no R1 network: missing", lob.status == "missing", lob.status)
    f += check("R1 guest network no SZ portal pairs with: listed",
               [u["name"] for u in rep.unpaired_r1_portals] == ["Pool Cabana"], str(rep.unpaired_r1_portals))

    print("\n5. Order")
    f += check("networks: portals first, then by name",
               [n.sz_wlan_name for n in rep.networks] == ["Lobby Portal", "Site Guest", "Alpha Unit", "Zeta Unit"],
               str([n.sz_wlan_name for n in rep.networks]))
    f += check("pairing list: same order",
               [m.sz_name for m in rep.matching.wlans] == ["Lobby Portal", "Site Guest", "Alpha Unit", "Zeta Unit"],
               str([m.sz_name for m in rep.matching.wlans]))

    print("\n6. WLAN Groups")
    carry = {u.name: u.r1_groups_carrying_all for u in rep.matching.wlan_groups}
    f += check("SSIDs read from the WLANs: 'A' carried by R1 group A", carry.get("A") == ["A"], str(carry))
    row = next((o for o in rep.overview if o.label.startswith("WLAN Groups")), None)
    f += check("summary row present beside AP Groups", row is not None and "carried in full" in row.r1_note,
               str(row))

    print(f"\n{'All checks passed' if not f else f'{f} check(s) failed'}")
    return f


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
