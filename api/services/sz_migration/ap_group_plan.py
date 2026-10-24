"""
Which R1 radios an SSID goes on, from the SZ radios it was broadcast on.

  radios: 2.4 -> 2.4-GHz; 5 / 5L / 5U -> 5-GHz; 6 -> 6-GHz only for
  WPA3/OWE WLANs (R1 refuses other security on 6 GHz).

Which R1 AP Groups each SSID goes on is r1_layout's job.
"""

from typing import Any, Dict, List, Optional, Tuple

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
