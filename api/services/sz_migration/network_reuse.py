"""
Which existing R1 network a migrated SZ WLAN should use -- the choice the
Review step shows and the plan honours.

R1 networks are tenant-wide and a network can broadcast on many venues, so a
WLAN whose SSID already has a network in R1 is offered that network: one
already on this venue (an earlier run) first, then one activated nowhere,
then a shared one on other venues. Creating a second network with the same
SSID (renamed "<name>_<venue>") is only the default when nothing reusable
exists -- e.g. the only same-SSID network is another property's with
another passphrase.

Passphrases are compared as fingerprints (passphrase_check); none is ever
returned from here.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.sz_migration.passphrase_check import compare_passphrases

logger = logging.getLogger(__name__)

# R1 nwSubType -> the plan's network types.
R1_SUBTYPE = {"PSK": "psk", "DPSK": "dpsk", "AAA": "aaa", "OPEN": "open", "GUEST": "wispr"}
READ_CONCURRENCY = 6


def plan_type(r1_type: Optional[str]) -> Optional[str]:
    """The network type the plan builds, as R1_SUBTYPE values (external DPSK is a DPSK network)."""
    return "dpsk" if r1_type == "dpsk_external" else r1_type


def network_venues(net: Dict[str, Any]) -> set:
    ids = set(net.get("venueIds") or [])
    ids |= {v.get("venueId") for v in net.get("venueApGroups") or [] if v.get("venueId")}
    return ids


def tenant_networks(inv) -> List[Dict[str, Any]]:
    """Every network in the tenant, once: this venue's full records over the slim tenant rows."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for n in [*inv.tenant_networks, *inv.wifi_networks]:
        if n.get("id"):
            by_id[n["id"]] = {**by_id.get(n["id"], {}), **n}
    return list(by_id.values())


def candidates(inv, ssid: str, want_type: Optional[str], name: str) -> List[Dict[str, Any]]:
    """
    R1 networks broadcasting exactly this SSID (SSIDs are case-sensitive), as
    review rows, best first: on this venue, then activated nowhere, then
    elsewhere; same type before another; SZ's name before another.
    passphrase is filled in by check_passphrases (PSK only).
    """
    rows = []
    for n in tenant_networks(inv):
        if n.get("ssid") != ssid:
            continue
        venues = network_venues(n)
        have = R1_SUBTYPE.get(str(n.get("nwSubType") or "").upper())
        rows.append({
            "network_id": n["id"],
            "name": n.get("name"),
            "type": have,
            "type_matches": have is None or want_type is None or have == plan_type(want_type),
            "on_this_venue": inv.venue_id in venues,
            "other_venues": len(venues - {inv.venue_id}),
            "passphrase": None,
        })
    rows.sort(key=lambda r: (not r["on_this_venue"], r["other_venues"] > 0, not r["type_matches"],
                             r["name"] != name, (r["name"] or "").casefold()))
    return rows


def ours(row: Dict[str, Any]) -> bool:
    """On this venue, or activated nowhere: an earlier run's or staged -- reusing it touches no other venue."""
    return row["on_this_venue"] or row["other_venues"] == 0


async def check_passphrases(r1_client, tenant_id: str, rows: List[Dict[str, Any]],
                            sz_encryption: Optional[Dict[str, Any]],
                            sem: Optional[asyncio.Semaphore] = None) -> None:
    """
    Sets row["passphrase"] = match | differs | unknown on each PSK row, in
    place. sem: shared across WLANs checked together (one R1 read each).
    """
    sem = sem or asyncio.Semaphore(READ_CONCURRENCY)

    async def one(row):
        async with sem:
            try:
                detail = await r1_client.networks.get_wifi_network_by_id(row["network_id"], tenant_id)
            except Exception as e:
                logger.warning(f"Reuse check: couldn't read R1 network {row['network_id']}: {e}")
                row["passphrase"] = "unknown"
                return
        status = compare_passphrases(sz_encryption, (detail or {}).get("wlan") or {})["status"]
        detail = None
        row["passphrase"] = ("match" if status == "match"
                             else "differs" if status in ("differs", "missing_in_r1") else "unknown")

    await asyncio.gather(*(one(r) for r in rows if r["type"] in (None, "psk")))


async def match_all(r1_client, tenant_id: str, inv, wlans: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    {wlan id: candidate rows} for wlans [{id, name, ssid, r1_type, encryption}],
    passphrases of PSK WLANs checked in one pass (a 300-WLAN zone is ~300 reads).
    """
    sem = asyncio.Semaphore(READ_CONCURRENCY)
    out = {w["id"]: candidates(inv, w["ssid"], w.get("r1_type"), w["name"]) for w in wlans}
    await asyncio.gather(*(
        check_passphrases(r1_client, tenant_id, out[w["id"]], w.get("encryption"), sem)
        for w in wlans if plan_type(w.get("r1_type")) == "psk" and out[w["id"]]
    ))
    return out


def suggest(rows: List[Dict[str, Any]], want_type: Optional[str], sz_has_passphrase: bool) -> Dict[str, Any]:
    """
    The default choice: {action: reuse, network_id, passphrase: sz | keep | None, reason}
    or {action: create, reason}.

    - A network of ours (this venue / nowhere): reused. A PSK one whose
      passphrase isn't SZ's gets SZ's (nothing else broadcasts it).
    - Else a shared one on other venues of the same type whose passphrase is
      SZ's (or not PSK): reused as it is -- that's what a tenant-wide network is for.
    - Else created; a same-SSID network that differs is listed to pick by hand.
    """
    psk = plan_type(want_type) == "psk"
    for row in rows:
        if not ours(row):
            continue
        set_sz = psk and row["passphrase"] != "match" and sz_has_passphrase and row["type_matches"]
        where = "already on this venue" if row["on_this_venue"] else "in R1, not activated anywhere"
        return {"action": "reuse", "network_id": row["network_id"], "passphrase": "sz" if set_sz else None,
                "reason": where + ("; its passphrase is set to SZ's" if set_sz else "")}
    for row in rows:
        if row["type_matches"] and (not psk or row["passphrase"] == "match"):
            return {"action": "reuse", "network_id": row["network_id"], "passphrase": None,
                    "reason": f"shared: already broadcast on {row['other_venues']} other venue(s)"
                              + ("; same passphrase as SZ" if psk else "")}
    if rows:
        r = rows[0]
        why = ("another type" if not r["type_matches"]
               else "another passphrase" if r["passphrase"] == "differs" else "a passphrase that couldn't be read")
        return {"action": "create",
                "reason": f"R1's '{r['name']}' has this SSID on {r['other_venues']} other venue(s) but {why} "
                          "— pick it to share it anyway"}
    return {"action": "create", "reason": "no R1 network has this SSID"}


def resolve(choice: Optional[Dict[str, Any]], rows: List[Dict[str, Any]], want_type: Optional[str],
            sz_has_passphrase: bool) -> Dict[str, Any]:
    """
    The operator's choice checked against what R1 has now, else the
    suggestion. Adds "row" (the chosen candidate) and "error" (a choice that
    can't be honoured -- the plan must not quietly do something else).
    """
    if not choice:
        out = suggest(rows, want_type, sz_has_passphrase)
        out["chosen_by"] = "suggested"
    elif choice.get("action") == "create":
        out = {"action": "create", "reason": "Create new chosen on Review", "chosen_by": "operator"}
    else:
        out = {"action": "reuse", "network_id": choice.get("network_id"),
               "passphrase": choice.get("passphrase"), "chosen_by": "operator", "reason": "chosen on Review"}
    if out["action"] == "reuse":
        out["row"] = next((r for r in rows if r["network_id"] == out["network_id"]), None)
        if out["row"] is None:
            out["error"] = ("the R1 network chosen to reuse no longer has this SSID (deleted or changed "
                            "since Review) — choose again")
        elif out.get("passphrase") == "sz" and not sz_has_passphrase:
            out["error"] = "SZ gave no passphrase to set on it"
        elif plan_type(want_type) != "psk" or out["row"]["type"] not in (None, "psk"):
            out["passphrase"] = None
    return out
