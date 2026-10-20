"""
Where each SZ AP Group lands in R1, and on which radios each SSID goes.

SZ: WLAN -> WLAN Group -> (zone | AP Group | AP) per radio.
R1: network -> venue -> specific AP Groups, with radio types per group.

This module covers the AP Group tier (phase 1). Per-AP overrides -- which in
R1 can only be reproduced by AP Group membership -- are not resolved here yet.

Rules:
  * SZ "default" AP Group  -> the venue's default R1 AP Group (unnamed, isDefault).
  * other SZ AP Groups     -> R1 AP Group of the same name, else planned for
                              creation (APs usually arrive in the venue after
                              the config migration, so the groups must exist
                              first for the activations to have a target).
  * SZ AP Groups with no APs broadcast nothing: not created, not activated.
  * radios: 2.4 -> 2.4-GHz; 5 / 5L / 5U -> 5-GHz; 6 -> 6-GHz only for
    WPA3/OWE WLANs (R1 refuses other security on 6 GHz).
"""

from typing import Any, Dict, List, Optional, Tuple

SZ_DEFAULT_AP_GROUP = "default"

_BAND_TO_R1 = {"2.4": "2.4-GHz", "5": "5-GHz", "5L": "5-GHz", "5U": "5-GHz", "6": "6-GHz"}
_SIX_GHZ_SECURITY = {"WPA3", "OWE"}
R1_RADIO_ORDER = ["2.4-GHz", "5-GHz", "6-GHz"]


def r1_radio_types(sz_bands: List[str], wlan_encryption: Optional[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """(R1 radioTypes, notes about bands that could not be carried over)."""
    method = str((wlan_encryption or {}).get("method") or "")
    wanted = {_BAND_TO_R1[b] for b in sz_bands if b in _BAND_TO_R1}
    notes: List[str] = []
    if "6-GHz" in wanted and method not in _SIX_GHZ_SECURITY:
        wanted.discard("6-GHz")
        notes.append(f"6 GHz not carried over: R1 allows only WPA3/OWE there (SZ security {method or 'unknown'})")
    return [r for r in R1_RADIO_ORDER if r in wanted], notes


def plan_ap_groups(sz_ap_groups, r1_inventory) -> Dict[str, Dict[str, Any]]:
    """
    SZ AP Group name -> {r1_id, r1_name, create, empty, error}.

    r1_id is None for groups to be created; create_ap_groups publishes their
    ids by r1_name for the activation phase.
    """
    plan: Dict[str, Dict[str, Any]] = {}
    for g in sz_ap_groups:
        name = g.name
        entry: Dict[str, Any] = {"r1_id": None, "r1_name": name, "create": False,
                                 "empty": (g.ap_count or 0) == 0, "error": None}
        if name == SZ_DEFAULT_AP_GROUP:
            d = r1_inventory.default_ap_group()
            if d is None:
                entry["error"] = "The R1 venue has no default AP Group"
            else:
                entry.update(r1_id=d.get("id"), r1_name=d.get("name") or "(default)")
        else:
            r1 = r1_inventory.ap_group_by_name(name)
            if r1:
                entry["r1_id"] = r1.get("id")
            elif not entry["empty"]:
                entry["create"] = True
        plan[name] = entry
    return plan
