"""
Captive portal WLANs, setting by setting: SZ portal WLAN + its portal
profile against the paired R1 guest network.

SZ keeps a portal in two places: the WLAN (type, auth / accounting
service and whether the controller proxies it, bypass CNA, VLAN...) and
the portal profile it points at (/portals/hotspot or /portals/guest: URLs,
walled garden, sessions, signing key...). R1 keeps it all on the network:
`guestPortal` (with `wisprPage`) and `wlan`, plus the RADIUS profiles
linked to it and their proxy settings (auth_facts).

Every setting is shown. The ones with an R1 counterpart are compared
(checks); the rest are listed per side (sz_only / r1_only) so nothing in
either portal goes unseen. Credentials -- the SZ signing key, the R1
integration key -- are reported as set / not set, never as values, and
compared as keyed fingerprints. SZ's API doesn't return the signing key,
so that row is usually "not verified" until the user checks a key against
live R1 (check_portal_key).

Pure: snapshot + inventory + matching + R1 detail in, models out.
"""

import hmac
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from schemas.migration_audit import AuditMatching, PortalAudit, PortalCheck
from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot
from services.sz_migration.audit_redaction import CREDENTIAL_PRESENT, is_secret_key
from services.sz_migration.passphrase_check import fingerprint

# The signing-key row; the page attaches the key-check result to it
KEY_ROW = "Signing key → integration key"
# SZ WLAN types that put clients behind a captive portal
PORTAL_WLAN_TYPES = {"Hotspot", "Hotspot_MacByPass", "Guest", "WebAuth"}
_WISPR_TYPES = {"Hotspot", "Hotspot_MacByPass"}
# Portal-profile fields that only identify it
_SZ_PROFILE_SKIP = {"id", "zoneId", "_portal_kind", "_error"}


def is_portal_wlan(wlan) -> bool:
    return (wlan.raw or {}).get("type") in PORTAL_WLAN_TYPES or bool((wlan.raw or {}).get("portalServiceProfile"))


def _flat(d: Any, pre: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{pre}{k}"
            if isinstance(v, dict) and not is_secret_key(k):
                out.update(_flat(v, key + "."))
            else:
                out[key] = v
    return out


def _shown(key: str, v: Any) -> str:
    """A value fit for the page: credentials as set / not set, lists joined."""
    if is_secret_key(key.rsplit(".", 1)[-1]):
        return "set" if v else "not set"
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "—"
    return str(v)


def _norm(v: Any) -> Any:
    if isinstance(v, str):
        return v.strip().rstrip("/").lower()
    return v


def _server(raw: Dict[str, Any], key: str) -> Optional[str]:
    from services.sz_migration.auditor import _sz_server
    return _sz_server(raw, key)


def _same_server(a: Optional[str], b: Optional[str]) -> Optional[bool]:
    from services.sz_migration.auditor import _same_server as same
    return same(a, b)


class _Checks:
    def __init__(self):
        self.rows: List[PortalCheck] = []
        self.used_sz: Set[str] = set()
        self.used_r1: Set[str] = set()

    def add(self, label: str, sz: Any, r1: Any, status: str, note: str = "",
            sz_keys: Iterable[str] = (), r1_keys: Iterable[str] = ()):
        self.used_sz.update(sz_keys)
        self.used_r1.update(r1_keys)
        self.rows.append(PortalCheck(label=label, sz=sz if isinstance(sz, str) else _shown(label, sz),
                                     r1=r1 if isinstance(r1, str) else _shown(label, r1),
                                     status=status, note=note))

    def compare(self, label: str, sz: Any, r1: Any, sz_keys=(), r1_keys=(), note: str = ""):
        both_empty = sz in (None, "", []) and r1 in (None, "", [])
        status = "match" if both_empty or _norm(sz) == _norm(r1) else "differs"
        self.add(label, sz, r1, status, note, sz_keys, r1_keys)


def _radius(facts: Dict[str, Any], net_id: str, role: str) -> Optional[Dict[str, Any]]:
    return next((p for p in (facts.get("radius_by_network") or {}).get(net_id, []) if p.get("type") == role), None)


def _key_fp(v: Any) -> Optional[str]:
    """Keyed fingerprint of a portal key (raw or already fingerprinted); None when absent or only marked present."""
    if not isinstance(v, str) or not v or v == CREDENTIAL_PRESENT:
        return None
    return fingerprint(v)


def _key_rows(c: "_Checks", prof: Dict[str, Any], gp: Dict[str, Any], wp: Dict[str, Any],
              key_labels: Dict[str, str]) -> None:
    """
    The WISPr signing key, compared as keyed fingerprints. SZ's API has
    returned it blank or null at every site so far, so usually only R1's side
    is known; the row then says "not verified" and points at the key check
    (the user supplies the key, the server compares it with live R1).
    """
    sz_keys = {k for k in prof if k.lower() in ("signaturesigningkey", "signingkey")}
    sz_raw = next((prof[k] for k in sz_keys if prof[k]), None)
    r1_raw = wp.get("integrationKey")
    sz_fp, r1_fp = _key_fp(sz_raw), _key_fp(r1_raw)
    encrypt = bool(wp.get("encryptMacIpEnabled"))
    r1_text = (f"set{f' ({key_labels[r1_fp]})' if r1_fp in key_labels else ''}" if r1_raw else "not set") \
        + f" · MAC/IP {'encrypted' if encrypt else 'not encrypted'}"
    sz_text = "set" if sz_raw else "not readable — SZ's API doesn't return it"

    if r1_raw == CREDENTIAL_PRESENT:
        status, note = "unknown", "This R1 capture predates key fingerprints — refresh the R1 side to compare"
    elif sz_fp and r1_fp:
        if sz_fp.split(":")[1] != r1_fp.split(":")[1]:
            status, note = "unknown", "Captured under different fingerprint keys — refresh to compare"
        else:
            status, note = ("match", "Same key on both sides") if sz_fp == r1_fp else \
                ("differs", "R1 holds a different key from SZ — the portal will reject R1's signed redirects")
    elif sz_raw and not r1_raw:
        status, note = "differs", "SZ signs with a key; R1 has none"
    else:
        status = "unknown"
        note = ("SZ won't hand the key out, so it can't be compared here — paste it into "
                "\"Check a portal key\" above to prove R1 holds the same one")
        if not r1_raw:
            note += ". R1 has no key: if the SZ portal uses one, R1 needs it too"
    c.add(KEY_ROW, sz_text, r1_text, status, note, sz_keys,
          {"wisprPage.integrationKey", "wisprPage.encryptMacIpEnabled"})

    ext_fp = _key_fp(gp.get("externalPortalKey"))
    if "externalPortalKey" in gp:
        c.used_r1.add("externalPortalKey")
        if ext_fp and r1_fp and ext_fp != r1_fp:
            c.add("R1 portal key = integration key", "", "differ", "differs",
                  "R1's externalPortalKey and wisprPage.integrationKey should be the same key")


def _audit_pair(wlan, portal_ref, net: Optional[Dict[str, Any]], facts: Optional[Dict[str, Any]],
                snapshot: SZMigrationSnapshot, key_labels: Optional[Dict[str, str]] = None) -> PortalAudit:
    key_labels = key_labels or {}
    raw = wlan.raw or {}
    prof = (portal_ref.raw if portal_ref else None) or {}
    prof_err = prof.get("_error")
    gp = (net or {}).get("guestPortal") or {}
    wp = gp.get("wisprPage") or {}
    nw = (net or {}).get("wlan") or {}
    sz_type = raw.get("type") or "?"
    out = PortalAudit(
        sz_wlan_id=wlan.id, sz_wlan_name=wlan.name, sz_ssid=wlan.ssid, sz_type=sz_type,
        sz_portal_name=(raw.get("portalServiceProfile") or {}).get("name") or (portal_ref.name if portal_ref else None),
        r1_network_id=(net or {}).get("id"), r1_name=(net or {}).get("name"),
        r1_guest_type=gp.get("guestNetworkType"),
    )
    if prof_err:
        out.notes.append("The SZ portal profile couldn't be read in this capture "
                         f"({str(prof_err)[:120]}) — refresh the SZ side; captures before 24 Oct read "
                         "hotspot portals from the wrong place")
    if net is None:
        out.status = "missing"
        out.notes.append("No paired R1 network (or its detail wasn't read) — nothing to compare")
        out.sz_only = [PortalCheck(label=k, sz=_shown(k, v), r1="", status="info")
                       for k, v in _flat(prof).items() if k not in _SZ_PROFILE_SKIP]
        return out

    c = _Checks()
    # ── Portal kind ──────────────────────────────────────────────
    if sz_type in _WISPR_TYPES:
        c.add("Portal type", f"{sz_type} (WISPr hotspot)", gp.get("guestNetworkType") or "—",
              "match" if gp.get("guestNetworkType") == "WISPr" else "differs",
              "" if gp.get("guestNetworkType") == "WISPr" else "An SZ hotspot is a WISPr guest network in R1",
              r1_keys={"guestNetworkType"})
    else:
        c.add("Portal type", f"{sz_type} (SZ guest / web-auth portal)", gp.get("guestNetworkType") or "—",
              "info", "No one-to-one R1 type; check the R1 guest network type by hand",
              r1_keys={"guestNetworkType"})

    # ── URLs ─────────────────────────────────────────────────────
    c.compare("Portal URL", prof.get("portalUrl"), gp.get("externalPortalUrl"),
              {"portalUrl"}, {"externalPortalUrl"})
    if "captivePortalUrl" in wp:
        c.compare("Captive portal URL (R1 WISPr page)", prof.get("portalUrl"), wp.get("captivePortalUrl"),
                  {"portalUrl"}, {"wisprPage.captivePortalUrl"})
    sz_redirect = (prof.get("redirect") or {}).get("url") or "(the URL the user asked for)"
    c.compare("Redirect after login", sz_redirect, gp.get("redirectUrl") or "(the URL the user asked for)",
              {"redirect.url"}, {"redirectUrl"})
    if prof.get("backupPortalUrl"):
        c.add("Backup portal URL", prof["backupPortalUrl"], "no R1 setting", "differs",
              "SZ fails over to a backup portal; R1 has no equivalent", sz_keys={"backupPortalUrl"})
    else:
        c.used_sz.add("backupPortalUrl")

    # ── Walled garden ────────────────────────────────────────────
    sz_wg = {str(x).strip().lower() for x in prof.get("walledGardens") or [] if str(x).strip()}
    r1_wg = {str(x).strip().lower() for x in gp.get("walledGardens") or [] if str(x).strip()}
    missing, extra = sorted(sz_wg - r1_wg), sorted(r1_wg - sz_wg)
    c.add("Walled garden", f"{len(sz_wg)} entries", f"{len(r1_wg)} entries",
          "match" if not (missing or extra) else "differs",
          "; ".join(x for x in (f"missing in R1: {', '.join(missing)}" if missing else "",
                                f"only in R1: {', '.join(extra)}" if extra else "") if x)
          or (", ".join(sorted(sz_wg)) if sz_wg else ""),
          {"walledGardens"}, {"walledGardens"})

    # ── Sessions ─────────────────────────────────────────────────
    us = prof.get("userSession") or {}
    c.compare("Session timeout (min)", us.get("timeoutInMin"), gp.get("userSessionTimeout"),
              {"userSession.timeoutInMin"}, {"userSessionTimeout"})
    c.compare("Grace period (min)", us.get("gracePeriodInMin"), gp.get("userSessionGracePeriod"),
              {"userSession.gracePeriodInMin"}, {"userSessionGracePeriod"})

    # ── WISPr provider and signing key ───────────────────────────
    if sz_type in _WISPR_TYPES:
        c.add("Provider name", out.sz_portal_name or "—", wp.get("externalProviderName") or "—",
              "match" if _norm(out.sz_portal_name) == _norm(wp.get("externalProviderName")) else "info",
              "R1 shows the SZ portal's name as the provider", r1_keys={"wisprPage.externalProviderName"})
        _key_rows(c, prof, gp, wp, key_labels)

    # ── Authentication / accounting ──────────────────────────────
    auth = raw.get("authServiceOrProfile") or {}
    acct = raw.get("accountingServiceOrProfile") or {}
    refs = snapshot.referenced_objects
    if facts is None:
        out.notes.append("RADIUS links weren't read this run (no live R1 connection) — auth not compared")
    else:
        r1_auth = _radius(facts, net["id"], "AUTHENTICATION")
        r1_acct = _radius(facts, net["id"], "ACCOUNTING")
        settings = (facts.get("radius_settings") or {}).get(net["id"]) or {}
        for role, sz_ref_raw, r1p, proxy_key in (("Authentication", auth, r1_auth, "enable_auth_proxy"),
                                                  ("Accounting", acct, r1_acct, "enable_accounting_proxy")):
            ref = refs.get(f"{'auth' if role == 'Authentication' else 'accounting'}_service:{sz_ref_raw.get('id')}") \
                if sz_ref_raw.get("id") else None
            sz_srv = _server(ref.raw, "primary") if ref else None
            r1_srv = (r1p or {}).get("primary")
            if not sz_ref_raw.get("id") and not r1p:
                c.add(f"{role} server", "none", "none", "match")
                continue
            same = _same_server(sz_srv, r1_srv)
            c.add(f"{role} server",
                  f"{sz_ref_raw.get('name') or '—'}{f' ({sz_srv})' if sz_srv else ''}" if sz_ref_raw.get("id") else "none",
                  f"{(r1p or {}).get('name')} ({r1_srv})" if r1p else "none",
                  "match" if same else "differs" if (same is False or bool(sz_ref_raw.get("id")) != bool(r1p)) else "unknown",
                  "" if same else "Compared by server address; names may differ (R1 adds a suffix)")
            if sz_ref_raw.get("id") and r1p:
                c.compare(f"{role} proxied through the controller",
                          bool(sz_ref_raw.get("throughController")), bool(settings.get(proxy_key)))
        if sz_type in _WISPR_TYPES:
            c.add("WISPr auth type", "RADIUS" if auth.get("id") else "—", wp.get("authType") or "—",
                  "match" if (wp.get("authType") == "RADIUS") == bool(auth.get("id")) else "differs",
                  r1_keys={"wisprPage.authType"})

    # ── WLAN-level portal behaviour ──────────────────────────────
    c.compare("Bypass CNA (captive network assistant)", bool(raw.get("bypassCNA")), bool(nw.get("bypassCNA")))
    c.compare("MAC auth bypasses the portal", sz_type == "Hotspot_MacByPass" or bool(raw.get("macAuth")),
              bool(nw.get("bypassCPUsingMacAddressAuthentication")))
    c.compare("VLAN", (raw.get("vlan") or {}).get("accessVlan"), nw.get("vlanId"))
    c.compare("Client isolation", bool((raw.get("advancedOptions") or {}).get("clientIsolationEnabled")),
              bool((nw.get("advancedCustomization") or {}).get("clientIsolation")))

    out.checks = c.rows
    # Everything else each side's portal holds, so nothing goes unseen
    out.sz_only = [PortalCheck(label=k, sz=_shown(k, v), r1="", status="info")
                   for k, v in _flat(prof).items() if k not in _SZ_PROFILE_SKIP and k not in c.used_sz]
    out.r1_only = [PortalCheck(label=k, sz="", r1=_shown(k, v), status="info")
                   for k, v in _flat(gp).items() if k not in c.used_r1]
    statuses = {r.status for r in c.rows}
    out.status = "differs" if "differs" in statuses else "unknown" if (prof_err or "unknown" in statuses) else "match"
    return out


def build_portal_audit(snapshot: SZMigrationSnapshot, inv: R1VenueInventory, matching: AuditMatching,
                       r1_network_details: Optional[Dict[str, Dict]],
                       auth_facts: Optional[Dict[str, Any]]) -> Tuple[List[PortalAudit], List[Dict[str, Any]]]:
    """(one PortalAudit per SZ portal WLAN, R1 guest networks in the venue no SZ portal pairs with)."""
    pairs = {m.sz_wlan_id: m for m in matching.wlans}
    details = r1_network_details or {}
    audits: List[PortalAudit] = []
    paired: Set[str] = set()
    # "key A", "key B": which of the venue's guest networks share an integration key
    key_labels: Dict[str, str] = {}
    for d in sorted(details.values(), key=lambda d: d.get("name") or ""):
        fp = _key_fp(((d.get("guestPortal") or {}).get("wisprPage") or {}).get("integrationKey"))
        if fp and fp not in key_labels:
            key_labels[fp] = f"key {chr(ord('A') + len(key_labels))}"
    for w in sorted((w for w in snapshot.wlans if is_portal_wlan(w)), key=lambda w: w.name.lower()):
        pid = ((w.raw or {}).get("portalServiceProfile") or {}).get("id")
        ref = snapshot.referenced_objects.get(f"portal_service_profile:{pid}") if pid else None
        m = pairs.get(w.id)
        net = details.get(m.r1_network_id) if m and m.r1_network_id else None
        if m and m.r1_network_id:
            paired.add(m.r1_network_id)
        audits.append(_audit_pair(w, ref, net, auth_facts, snapshot, key_labels))
    venue_ids = {n.get("id") for n in inv.wifi_networks}
    unpaired = [{"id": nid, "name": d.get("name"), "ssid": (d.get("wlan") or {}).get("ssid"),
                 "guest_type": (d.get("guestPortal") or {}).get("guestNetworkType")}
                for nid, d in details.items()
                if nid in venue_ids and nid not in paired and d.get("type") == "guest"]
    return audits, sorted(unpaired, key=lambda u: (u["name"] or "").lower())


KEY_CHECK_MAX_NETWORKS = 50


def check_portal_key(entered: str, network: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """
    Does this R1 guest network hold the key the user entered? {status, note}
    only -- status: match | differs | not_set | not_read. Neither key is
    returned. Both R1 copies (wisprPage.integrationKey, externalPortalKey)
    must match.
    """
    if network is None:
        return {"status": "not_read", "note": "Couldn't read this network from R1"}
    gp = network.get("guestPortal") or {}
    held = [(name, v) for name, v in (("integration key", (gp.get("wisprPage") or {}).get("integrationKey")),
                                      ("external portal key", gp.get("externalPortalKey"))) if v]
    if not held:
        return {"status": "not_set", "note": "R1 has no key on this network"}
    wrong = [name for name, v in held if not hmac.compare_digest(str(v).encode(), entered.encode())]
    if not wrong:
        encrypt = bool((gp.get("wisprPage") or {}).get("encryptMacIpEnabled"))
        return {"status": "match", "note": "R1 holds exactly this key" + ("" if encrypt else
                " — but MAC/IP encryption is off, so R1 doesn't use it")}
    if len(wrong) < len(held):
        return {"status": "differs", "note": f"R1's {wrong[0]} differs; the other copy matches"}
    return {"status": "differs", "note": "R1 holds a different key"}
