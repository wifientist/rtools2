"""
Compare RADIUS attribute groups across tenants and against a template.

Verified live 2026-10-30 (SuperSandbox, PROD-Comcast-MSP-ro):

- GET /radiusAttributeGroups pages at 10; /query is 0-indexed on the way in and
  echoes page+1 on the way out, so stop on totalCount, not the echo.
- Unfiltered /radiusAttributes/query returns 51 of 522 attributes and no WISPr
  at all. Asking per vendor (filters.vendorName as a string -- a list 500s) is
  what returns them.
- Group names are case-sensitive: "FAST" and "fast" can coexist.
- PATCH replaces the whole attribute list, takes effect synchronously, and
  answers 200 for an attribute name that does not exist. Nothing but this
  module's checks stands between a typo and a group that returns nothing.

The readers here are sync on purpose: R1Client is `requests`, so callers hop to
a thread rather than stalling the event loop across a whole MSP's tenants.
"""

import math
from collections import Counter
from typing import Any, Dict, List, Optional

PAGE_SIZE = 100


def _error(response, what: str) -> Exception:
    try:
        body = response.json()
        errors = body.get("errors") or []
        detail = "; ".join(e.get("message") or str(e) for e in errors) or body.get("message")
    except Exception:
        detail = None
    return Exception(f"{what} failed ({response.status_code}): {detail or response.text[:200]}")


def _scope(r1_client, tenant_id: Optional[str]) -> Dict[str, str]:
    return {"override_tenant_id": tenant_id} if r1_client.ec_type == "MSP" and tenant_id else {}


def read_tenant_groups(r1_client, tenant_id: Optional[str]) -> List[Dict[str, Any]]:
    """Every group in the tenant. Raises rather than returning a short list:
    an unread tenant must not look like one where every group is missing."""
    groups: List[Dict[str, Any]] = []
    page = 0
    while True:
        response = r1_client.post(
            "/radiusAttributeGroups/query",
            payload={"page": page, "pageSize": PAGE_SIZE},
            **_scope(r1_client, tenant_id),
        )
        if not response.ok:
            raise _error(response, "Reading RADIUS groups")
        body = response.json()
        rows = body.get("data") or []
        groups.extend(rows)
        if not rows or len(groups) >= (body.get("totalCount") or 0):
            return groups
        page += 1


def read_attribute_catalogue(r1_client, tenant_id: Optional[str]) -> List[Dict[str, Any]]:
    """Every attribute R1 knows: name, vendor, data type."""
    scope = _scope(r1_client, tenant_id)
    response = r1_client.get("/radiusAttributes/vendors", **scope)
    if not response.ok:
        raise _error(response, "Reading RADIUS vendors")
    catalogue: List[Dict[str, Any]] = []
    for vendor in response.json().get("supportedVendors") or []:
        page = 0
        rows_seen = 0
        while True:
            response = r1_client.post(
                "/radiusAttributes/query",
                payload={"page": page, "pageSize": 500, "filters": {"vendorName": vendor}},
                **scope,
            )
            if not response.ok:
                raise _error(response, f"Reading {vendor} RADIUS attributes")
            body = response.json()
            rows = body.get("data") or []
            catalogue.extend(
                {"name": r["name"], "vendorName": r.get("vendorName") or vendor, "dataType": r.get("dataType")}
                for r in rows
            )
            rows_seen += len(rows)
            if not rows or rows_seen >= (body.get("totalCount") or 0):
                break
            page += 1
    return catalogue


def _key(attribute: Dict[str, Any]) -> tuple:
    # dataType is left out: it follows from the attribute name.
    return (
        attribute.get("attributeName") or "",
        attribute.get("operator") or "",
        str(attribute.get("attributeValue") if attribute.get("attributeValue") is not None else ""),
    )


def _attribute_rows(expected: List[Dict[str, Any]], actual: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per attribute name, in the standard's order, then the tenant's extras."""
    names = list(dict.fromkeys(a.get("attributeName") or "" for a in [*expected, *actual]))
    rows = []
    for name in names:
        want = [{"operator": k[1], "value": k[2]} for k in map(_key, expected) if k[0] == name]
        have = [{"operator": k[1], "value": k[2]} for k in map(_key, actual) if k[0] == name]
        same = Counter((w["operator"], w["value"]) for w in want) == Counter((h["operator"], h["value"]) for h in have)
        rows.append({"attributeName": name, "expected": want, "actual": have, "same": same})
    return rows


def _clean(attribute: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "attributeName": attribute.get("attributeName"),
        "operator": attribute.get("operator"),
        "attributeValue": attribute.get("attributeValue"),
        "dataType": attribute.get("dataType"),
    }


def analyze_consistency(tenants: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Which tenants agree most with the rest, to suggest a template.

    A group counts once at least half the tenants (and at least two) have it, so
    one tenant's stray group can't pull everyone's score down. Its consensus is
    its most common set of values. A tie has no consensus, so it is reported but
    not scored rather than quietly favouring whichever side sorted first.

    tenants: [{"id", "name", "groups": [raw R1 groups]}]
    """
    variants: Dict[str, Dict[tuple, Dict[str, Any]]] = {}
    for tenant in tenants:
        for group in tenant["groups"]:
            attributes = group.get("attributeAssignments") or []
            fingerprint = tuple(sorted(map(_key, attributes)))
            variant = variants.setdefault(group.get("name"), {}).setdefault(
                fingerprint, {"attributes": [_clean(a) for a in attributes], "tenant_ids": []},
            )
            variant["tenant_ids"].append(tenant["id"])

    common_at = max(2, math.ceil(len(tenants) / 2))
    groups = []
    consensus: Dict[str, tuple] = {}
    common = set()
    for name, found in variants.items():
        ordered = sorted(found.items(), key=lambda kv: (-len(kv[1]["tenant_ids"]), kv[0]))
        count = sum(len(v["tenant_ids"]) for v in found.values())
        tied = len(ordered) > 1 and len(ordered[0][1]["tenant_ids"]) == len(ordered[1][1]["tenant_ids"])
        is_common = count >= common_at
        if is_common:
            common.add(name)
            if not tied:
                consensus[name] = ordered[0][0]
        groups.append({
            "name": name,
            "tenant_count": count,
            "common": is_common,
            "tied": tied,
            "variants": [
                {**v, "majority": i == 0 and not tied} for i, (_, v) in enumerate(ordered)
            ],
        })
    groups.sort(key=lambda g: (-g["tenant_count"], g["name"] or ""))

    ranked = []
    for tenant in tenants:
        own = {g.get("name"): tuple(sorted(map(_key, g.get("attributeAssignments") or []))) for g in tenant["groups"]}
        agrees = sorted(n for n, want in consensus.items() if own.get(n) == want)
        differs = sorted(n for n, want in consensus.items() if n in own and own[n] != want)
        lacks = sorted(n for n in consensus if n not in own)
        ranked.append({
            "id": tenant["id"],
            "name": tenant["name"],
            "agrees": agrees,
            "differs": differs,
            "lacks": lacks,
            "other_groups": sorted(n for n in own if n not in common),
            "score": len(agrees) / len(consensus) if consensus else None,
        })
    ranked.sort(key=lambda t: (-(t["score"] or 0), len(t["differs"]), len(t["other_groups"]), t["name"] or ""))
    return {"tenants": ranked, "groups": groups, "scored_groups": len(consensus)}


def compare_tenant(standard_groups: List[Dict[str, Any]], tenant_groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The standard against one tenant. Name matching is exact, as it is in R1."""
    by_name = {g.get("name"): g for g in tenant_groups}
    wanted = {g["name"] for g in standard_groups}
    groups = []
    for standard in standard_groups:
        expected = standard.get("attributes") or []
        found = by_name.get(standard["name"])
        if found is None:
            similar = next(
                (g.get("name") for g in tenant_groups
                 if (g.get("name") or "").lower() == standard["name"].lower()),
                None,
            )
            groups.append({
                "name": standard["name"], "status": "missing", "group_id": None, "in_use": 0,
                "similar_name": similar, "attributes": _attribute_rows(expected, []),
            })
            continue
        actual = found.get("attributeAssignments") or []
        rows = _attribute_rows(expected, actual)
        groups.append({
            "name": standard["name"],
            "status": "match" if all(r["same"] for r in rows) else "differs",
            "group_id": found.get("id"),
            "in_use": found.get("externalAssignmentsCount") or 0,
            "similar_name": None,
            "attributes": rows,
        })

    extras = [
        {
            "name": g.get("name"),
            "in_use": g.get("externalAssignmentsCount") or 0,
            "attributes": [_clean(a) for a in g.get("attributeAssignments") or []],
        }
        for g in tenant_groups if g.get("name") not in wanted
    ]
    return {"groups": groups, "extras": extras}
