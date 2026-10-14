"""
DPSK service choices for an SZ → R1 migration.

Every WLAN that will be a DPSK network in R1 -- native SZ DPSK, or a RADIUS
WLAN converted on Review -- needs a DPSK service (pool) behind it, and the
pool needs an identity group. Instead of silently find-or-creating
"{wlan}-pool" / "{wlan}-identities" by name, Review now decides:

    pools:        pool_key -> spec
                  spec.mode "new":      name, settings, identity group
                                        (new by name, or an existing group
                                        not bound to any pool)
                  spec.mode "existing": an R1 pool id
    assignments:  wlan_id -> pool_key   (several WLANs may share one pool)

R1 rules this enforces up front, so a run can't fail halfway:
    * an identity group belongs to exactly one pool (DPSK-10032)
    * pool and identity group names are unique in the tenant
    * deviceCountLimit 1..512 (None = unlimited)

New pools are created by the global create_dpsk_pools phase -- not per WLAN,
since units run concurrently and two WLANs sharing a new pool would race to
create it. configure_dpsk then only links the assigned pool to each network.

Nothing here touches passphrases: a new pool starts empty, and filling it is
the Cloudpath import's job.
"""

from typing import Any, Dict, List, Optional, Tuple

from workflow.v2.models import ConflictDetail, ResourceAction

PASSPHRASE_FORMATS = ("KEYBOARD_FRIENDLY", "MOST_SECURED", "NUMBERS_ONLY", "DICTIONARY_WORDS")
MIN_PASSPHRASE_LENGTH, MAX_PASSPHRASE_LENGTH = 8, 63   # WPA2 PSK bounds
MAX_DEVICES = 512

DEFAULT_SETTINGS = {
    "passphrase_length": 12,
    "passphrase_format": "KEYBOARD_FRIENDLY",
    "devices_per_passphrase": None,   # unlimited; today's hard-coded 1 surprised BYOD users
    "expiration_days": None,
}


def default_plan(zone_name: str, dpsk_wlan_ids: List[str]) -> Dict[str, Any]:
    """One shared new pool + new identity group for every DPSK WLAN."""
    base = "".join(c if c.isalnum() or c in "-_" else "-" for c in zone_name).strip("-") or "migrated"
    return {
        "pools": {
            "shared": {
                "mode": "new",
                "name": f"{base}-dpsk",
                "identity_group": {"mode": "new", "name": f"{base}-identities"},
                **DEFAULT_SETTINGS,
            }
        },
        "assignments": {wid: "shared" for wid in dpsk_wlan_ids},
    }


def pool_summary(p: Dict[str, Any], groups_by_pool: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """An R1 pool as the UI may see it: settings and counts, never a passphrase."""
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "passphrase_length": p.get("passphraseLength"),
        "passphrase_format": p.get("passphraseFormat"),
        "devices_per_passphrase": p.get("deviceCountLimit"),
        "network_count": p.get("networkCount"),
        "identity_groups": [
            {"id": g.get("id"), "name": g.get("name"), "identity_count": g.get("identityCount")}
            for g in groups_by_pool.get(p.get("id"), [])
        ],
    }


def group_summary(g: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": g.get("id"),
        "name": g.get("name"),
        "dpsk_pool_id": g.get("dpskPoolId") or None,
        "identity_count": g.get("identityCount"),
    }


def validate_dpsk_plan(
    pools: Dict[str, Dict[str, Any]],
    assignments: Dict[str, str],
    dpsk_wlans: Dict[str, str],                 # wlan_id -> wlan name
    r1_pools: Dict[str, Dict[str, Any]],        # id -> pool_summary
    r1_groups: Dict[str, Dict[str, Any]],       # id -> group_summary
) -> Tuple[List[ConflictDetail], List[ResourceAction]]:
    conflicts: List[ConflictDetail] = []
    actions: List[ResourceAction] = []

    def err(name: str, msg: str, severity: str = "error", unit_id: Optional[str] = None):
        conflicts.append(ConflictDetail(
            unit_id=unit_id, resource_type="dpsk_pool", resource_name=name,
            description=msg, severity=severity,
        ))

    # Every DPSK WLAN needs a pool that exists in the plan.
    for wid, wname in dpsk_wlans.items():
        key = assignments.get(wid)
        if not key:
            err(wname, f"{wname} — will be a DPSK network but has no DPSK service chosen", unit_id=wid)
        elif key not in pools:
            err(wname, f"{wname} — assigned to a DPSK service that isn't in the plan", unit_id=wid)

    used_keys = {assignments[w] for w in dpsk_wlans if assignments.get(w) in pools}
    pool_names_by_lower = {(p.get("name") or "").lower(): p for p in r1_pools.values()}
    group_names_by_lower = {(g.get("name") or "").lower(): g for g in r1_groups.values()}
    new_pool_names: Dict[str, str] = {}
    new_group_names: Dict[str, str] = {}
    claimed_groups: Dict[str, str] = {}

    for key in sorted(used_keys):
        spec = pools[key]
        users = ", ".join(sorted(dpsk_wlans[w] for w in dpsk_wlans if assignments.get(w) == key))

        if spec.get("mode") == "existing":
            p = r1_pools.get(spec.get("existing_pool_id") or "")
            if p is None:
                err(key, f"DPSK service for {users} — the chosen R1 pool no longer exists")
                continue
            if not p["identity_groups"]:
                err(p["name"], f"R1 pool '{p['name']}' has no identity group — nobody can "
                               f"authenticate to {users}", severity="warning")
            actions.append(ResourceAction(
                resource_type="dpsk_pool", name=p["name"], action="reuse", existing_id=p["id"],
                notes=[f"For {users}"],
            ))
            continue

        # ── new pool ──────────────────────────────────────────────────
        name = (spec.get("name") or "").strip()
        if not name:
            err(key, f"New DPSK service for {users} needs a name")
            continue
        if name.lower() in pool_names_by_lower:
            err(name, f"R1 already has a DPSK pool named '{name}' — use it, or pick another name")
        if name.lower() in new_pool_names:
            err(name, f"Two new DPSK services are both named '{name}'")
        new_pool_names[name.lower()] = key

        length = spec.get("passphrase_length")
        if not isinstance(length, int) or not MIN_PASSPHRASE_LENGTH <= length <= MAX_PASSPHRASE_LENGTH:
            err(name, f"'{name}' — passphrase length must be {MIN_PASSPHRASE_LENGTH}–{MAX_PASSPHRASE_LENGTH}")
        if spec.get("passphrase_format") not in PASSPHRASE_FORMATS:
            err(name, f"'{name}' — unknown passphrase format {spec.get('passphrase_format')!r}")
        devices = spec.get("devices_per_passphrase")
        if devices is not None and (not isinstance(devices, int) or not 1 <= devices <= MAX_DEVICES):
            err(name, f"'{name}' — devices per passphrase must be 1–{MAX_DEVICES}, or unlimited")
        days = spec.get("expiration_days")
        if days is not None and (not isinstance(days, int) or days < 1):
            err(name, f"'{name}' — expiration must be a positive number of days, or none")

        ig = spec.get("identity_group") or {}
        notes = [f"For {users}"]
        if ig.get("mode") == "existing":
            g = r1_groups.get(ig.get("id") or "")
            if g is None:
                err(name, f"'{name}' — the chosen identity group no longer exists")
            elif g.get("dpsk_pool_id"):
                bound = r1_pools.get(g["dpsk_pool_id"], {}).get("name") or g["dpsk_pool_id"]
                err(name, f"Identity group '{g['name']}' already belongs to DPSK pool '{bound}' — "
                          "a group can serve only one pool (DPSK-10032)")
            elif g["id"] in claimed_groups:
                err(name, f"Identity group '{g['name']}' is chosen for two new DPSK services")
            else:
                claimed_groups[g["id"]] = key
                notes.append(f"identity group '{g['name']}' (existing, "
                             f"{g.get('identity_count') or 0} identities)")
        else:
            ig_name = (ig.get("name") or "").strip()
            if not ig_name:
                err(name, f"'{name}' — the new identity group needs a name")
            elif ig_name.lower() in group_names_by_lower:
                err(name, f"R1 already has an identity group named '{ig_name}' — "
                          "choose it as existing, or pick another name")
            elif ig_name.lower() in new_group_names:
                err(name, f"Two new identity groups are both named '{ig_name}'")
            else:
                new_group_names[ig_name.lower()] = key
                notes.append(f"new identity group '{ig_name}'")
                actions.append(ResourceAction(
                    resource_type="identity_group", name=ig_name, action="create",
                    notes=[f"For DPSK service '{name}'"],
                ))

        notes.append(
            f"{spec.get('passphrase_length')}-char {spec.get('passphrase_format')}, "
            f"{devices if devices else 'unlimited'} device(s)/passphrase, "
            f"{f'expires after {days} days' if days else 'no expiry'}"
        )
        actions.append(ResourceAction(resource_type="dpsk_pool", name=name, action="create", notes=notes))
        err(name, f"New DPSK service '{name}' starts with no passphrases — {users} can't be "
                  "joined until they are imported (e.g. with the Cloudpath import)", severity="warning")

    return conflicts, actions


async def load_r1_dpsk(r1_client, tenant_id: Optional[str]) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    """
    The tenant's DPSK pools and identity groups, reduced to summaries.

    Pools: /dpskServices/query is 1-indexed (page=0 is a bare 500).
    Groups: /identityGroups/query is a Spring page -- rows under "content",
    0-indexed, and it ignores searchString, so all of them are listed.
    """
    groups: List[Dict[str, Any]] = []
    page = 0
    while True:
        resp = await r1_client.identity.query_identity_groups(tenant_id=tenant_id, page=page, size=1000)
        rows = (resp.get("content", resp.get("data", [])) if isinstance(resp, dict) else []) or []
        groups.extend(rows)
        if not rows or not isinstance(resp, dict) or resp.get("last", True) or page > 50:
            break
        page += 1

    pools: List[Dict[str, Any]] = []
    page = 1
    while True:
        resp = await r1_client.dpsk.query_dpsk_pools(tenant_id=tenant_id, page=page, limit=100)
        rows = (resp or {}).get("data", []) if isinstance(resp, dict) else []
        pools.extend(rows)
        total = (resp or {}).get("totalCount", len(pools)) if isinstance(resp, dict) else len(pools)
        if not rows or len(pools) >= total or page > 50:
            break
        page += 1

    groups_by_pool: Dict[str, List[Dict[str, Any]]] = {}
    for g in groups:
        if g.get("dpskPoolId"):
            groups_by_pool.setdefault(g["dpskPoolId"], []).append(g)

    return (
        {p["id"]: pool_summary(p, groups_by_pool) for p in pools if p.get("id")},
        {g["id"]: group_summary(g) for g in groups if g.get("id")},
    )
