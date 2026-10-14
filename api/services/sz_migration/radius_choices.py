"""
RADIUS choices for an SZ → R1 migration.

R1 RADIUS server profiles are tenant-wide, but a migration is one zone into
one venue. So the migration only ever considers the auth/accounting services
that WLANs IN THIS ZONE reference, and for each one the user picks:

    create    -- a new R1 profile from the SZ servers
    existing  -- link an R1 profile that is already there
    skip      -- don't migrate it; each WLAN using it then either is skipped
                 or becomes an R1 DPSK network (wlan_fallbacks)

Shared secrets: SZ never returns them. On "create" the user may type one;
it goes to Redis under its own key (never into job options, which are
persisted and shown), is read once by the create_radius phase, and deleted.
Nothing here ever returns a secret -- R1 profile rows are reduced to
id/name/type and "ip:port" strings before they leave this module.
"""

from typing import Any, Dict, List, Optional

from schemas.sz_migration import SZMigrationSnapshot

# Redis hash: sz_service_id -> secret, for one migration job. Deleted by the
# create_radius phase after use; the TTL covers a plan that is never confirmed.
RADIUS_SECRET_KEY = "sz_migration:radius_secret:{job_id}"
RADIUS_SECRET_TTL = 6 * 3600

# Written when no shared secret is given. A profile still carrying it rejects
# every request; Review flags such profiles and never suggests linking one.
PLACEHOLDER_RADIUS_SECRET = "CHANGE_ME_after_migration"

ACTIONS = ("create", "existing", "skip")
WLAN_FALLBACKS = ("skip", "dpsk")


def _server(raw: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    """SZ server block → {ip, port}, or None."""
    s = raw.get(key)
    if isinstance(s, dict):
        ip = s.get("ip") or s.get("ipFqdn") or s.get("server")
        if ip:
            return {"ip": ip, "port": s.get("port") or 1812}
    elif key == "primary" and raw.get("ip"):
        return {"ip": raw["ip"], "port": raw.get("port") or 1812}
    return None


def _hostport(s: Optional[Dict[str, Any]]) -> Optional[str]:
    return f"{s['ip']}:{s['port']}" if s else None


def referenced_services(snapshot: SZMigrationSnapshot) -> List[Dict[str, Any]]:
    """
    Every RADIUS service the zone's WLANs reference, with who uses it.

    Returns [{id, name, roles: [authentication|accounting], primary, secondary,
              used_by: [{wlan_id, wlan_name, auth_type, role}], extraction_error}]
    """
    services: Dict[str, Dict[str, Any]] = {}
    for w in snapshot.wlans:
        for role, sid in (("authentication", w.auth_service_id),
                          ("accounting", w.accounting_service_id)):
            if not sid:
                continue
            entry = services.get(sid)
            if entry is None:
                ref_type = "auth_service" if role == "authentication" else "accounting_service"
                ref = snapshot.referenced_objects.get(f"{ref_type}:{sid}")
                raw = (ref.raw if ref else {}) or {}
                entry = services[sid] = {
                    "id": sid,
                    "name": (ref.name if ref else None) or raw.get("name") or f"service-{sid[:8]}",
                    "roles": [],
                    "primary": _hostport(_server(raw, "primary")),
                    "secondary": _hostport(_server(raw, "secondary")),
                    "used_by": [],
                    "extraction_error": bool(raw.get("_error")) or ref is None,
                }
            if role not in entry["roles"]:
                entry["roles"].append(role)
            entry["used_by"].append({
                "wlan_id": w.id, "wlan_name": w.name, "auth_type": w.auth_type, "role": role,
            })
    return list(services.values())


def r1_profile_summary(p: Dict[str, Any]) -> Dict[str, Any]:
    """
    An R1 RADIUS profile reduced to what the UI may see: no sharedSecret, ever.

    placeholder_secret says whether a server still carries the migration's
    placeholder -- computed here from the full row, so only the yes/no leaves.
    """
    def hp(s):
        if isinstance(s, dict):
            ip = s.get("ip") or s.get("hostname")
            return f"{ip}:{s.get('port')}" if ip and s.get("port") else ip
        return s if isinstance(s, str) else None
    servers = [s for s in (p.get("primary"), p.get("secondary")) if isinstance(s, dict)]
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "type": p.get("type"),
        "primary": hp(p.get("primary")),
        "secondary": hp(p.get("secondary")),
        "placeholder_secret": any(s.get("sharedSecret") == PLACEHOLDER_RADIUS_SECRET for s in servers),
    }


def suggest(service: Dict[str, Any], r1_profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    A starting choice: link an R1 profile on the same primary server (then
    same name), else create. Only a suggestion -- the UI shows it pre-selected.
    """
    want_type = "ACCOUNTING" if service["roles"] == ["accounting"] else "AUTHENTICATION"
    candidates = [p for p in r1_profiles if (p.get("type") or want_type) == want_type]
    # A profile still on the placeholder secret authenticates nobody: never
    # suggest linking one while a working profile on the same server exists.
    usable = [p for p in candidates if not p.get("placeholder_secret")]
    if service.get("primary"):
        for p in usable:
            if p.get("primary") == service["primary"]:
                return {"action": "existing", "r1_profile_id": p["id"],
                        "reason": f"R1 profile '{p['name']}' uses the same server"}
    for p in candidates:
        if p.get("name") == service["name"]:
            reason = f"R1 already has a profile named '{p['name']}'"
            if p.get("placeholder_secret"):
                reason += (" — it still has the placeholder secret: set the secret in R1, "
                           "or delete it there and choose Create")
            return {"action": "existing", "r1_profile_id": p["id"], "reason": reason}
    if not service.get("primary"):
        return {"action": "skip", "r1_profile_id": None,
                "reason": "No server address in the SZ snapshot to create from"}
    return {"action": "create", "r1_profile_id": None, "reason": "No matching R1 profile"}


def sz_server_details(snapshot: SZMigrationSnapshot, service_id: str) -> Dict[str, Any]:
    """{name, primary: {ip, port}|None, secondary: {ip, port}|None, role} for the create phase."""
    for ref_type, role in (("auth_service", "AUTHENTICATION"), ("accounting_service", "ACCOUNTING")):
        ref = snapshot.referenced_objects.get(f"{ref_type}:{service_id}")
        if ref is not None:
            raw = ref.raw or {}
            return {
                "name": ref.name or raw.get("name") or f"migrated-radius-{service_id[:8]}",
                "primary": _server(raw, "primary"),
                "secondary": _server(raw, "secondary"),
                "role": role,
                "error": bool(raw.get("_error")),
            }
    return {"name": f"migrated-radius-{service_id[:8]}", "primary": None,
            "secondary": None, "role": "AUTHENTICATION", "error": True}
