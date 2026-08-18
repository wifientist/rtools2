"""
RF estimation helpers for the Maps overlay.

Everything in here is an *estimate*. We have exactly one measurement per client
— the RSSI the serving AP sees — so we can estimate how far away a client is,
but not which direction it lies in. Callers must present these numbers as
approximations; see estimate_distance_m for the specific caveats.
"""

import hashlib
import math
from typing import Any, Dict, List, Optional, Tuple

# Log-distance path loss model defaults.
DEFAULT_PATH_LOSS_EXPONENT = 3.0   # typical indoor office/multi-unit
DEFAULT_CLIENT_TX_POWER_DBM = 15.0  # phones/laptops usually sit near 15 dBm

# Distances outside this range are model artifacts, not measurements.
MIN_DISTANCE_M = 0.5
MAX_DISTANCE_M = 100.0

# RSSI (dBm, measured at the AP) health tiers. Ordered strongest → weakest;
# the UI ramp and legend are built from this same ordering.
RSSI_TIERS = [
    ("excellent", -60),   # >= -60
    ("good", -70),        # -70 .. -60
    ("fair", -80),        # -80 .. -70
    ("poor", None),       # < -80
]


def rssi_tier(rssi: Optional[float]) -> str:
    """Bucket an RSSI reading into a health tier name."""
    if rssi is None:
        return "unknown"
    for name, floor in RSSI_TIERS:
        if floor is None or rssi >= floor:
            return name
    return "poor"


def channel_to_freq_mhz(channel: Optional[int], band: Optional[str]) -> Optional[float]:
    """
    Centre frequency for a channel, using `band` to break the 2.4/6 GHz overlap.

    Channels 1-14 are valid in both the 2.4 GHz and 6 GHz plans, so the band
    string decides. When band is missing we assume 2.4 GHz for low channel
    numbers, which is the far more common case in deployed fleets.
    """
    if channel is None:
        return None

    b = (band or "").lower().replace("_", ".").replace(" ", "")
    is_6ghz = "6" in b and "ghz" in b
    is_5ghz = "5" in b and "ghz" in b and not is_6ghz

    if is_6ghz:
        return 5950 + channel * 5
    if is_5ghz or channel >= 32:
        return 5000 + channel * 5
    if channel == 14:
        return 2484.0
    if 1 <= channel <= 13:
        return 2412 + (channel - 1) * 5
    return None


def band_to_freq_mhz(band: Optional[str]) -> float:
    """Representative centre frequency when the channel isn't known."""
    b = (band or "").lower().replace("_", ".").replace(" ", "")
    if "6" in b:
        return 6500.0
    if "5" in b:
        return 5500.0
    return 2437.0  # 2.4 GHz, channel 6


def path_loss_at_1m_db(freq_mhz: float) -> float:
    """
    Free-space path loss at the 1 m reference distance.

    FSPL(dB) = 20·log10(f_MHz) + 20·log10(d_km) + 32.44; at d = 1 m the
    distance term is 20·log10(0.001) = -60, collapsing to 20·log10(f) - 27.55.
    """
    return 20 * math.log10(freq_mhz) - 27.55


def estimate_distance_m(
    rssi: Optional[float],
    freq_mhz: float,
    path_loss_exponent: float = DEFAULT_PATH_LOSS_EXPONENT,
    client_tx_power_dbm: float = DEFAULT_CLIENT_TX_POWER_DBM,
) -> Optional[float]:
    """
    Estimate AP↔client distance from the RSSI the AP measured.

    Log-distance model: RSSI = Tx - PL(1m) - 10·n·log10(d), solved for d.

    Three things make this an estimate and not a measurement:

    1. **Client Tx power is unknown.** The AP reports received power, not what
       the client transmitted. We assume a constant (default 15 dBm); a client
       running power-save or a low-power IoT radio will read as further away
       than it is.
    2. **The path loss exponent is a guess about the building.** n=2 is free
       space, n=3 typical indoor, n=4+ heavy obstruction. One n is applied to
       the whole floor, so a client behind a lift shaft reads as distant.
    3. **No direction.** This is a radius only. Bearing is not recoverable from
       a single AP's RSSI.

    Returns metres, clamped to [MIN_DISTANCE_M, MAX_DISTANCE_M], or None if the
    RSSI is missing.
    """
    if rssi is None:
        return None

    loss_db = client_tx_power_dbm - path_loss_at_1m_db(freq_mhz) - rssi
    exponent = loss_db / (10.0 * max(path_loss_exponent, 0.1))
    try:
        distance = 10.0 ** exponent
    except OverflowError:
        return MAX_DISTANCE_M

    return max(MIN_DISTANCE_M, min(MAX_DISTANCE_M, distance))


def stable_bearing_deg(seed: str) -> float:
    """
    A fixed pseudo-bearing for a client, derived from its MAC.

    The direction is *not* real — we can't recover it from one RSSI. It exists
    so each client occupies its own slot on its AP's ring instead of every dot
    piling up at one point, and it's hashed rather than random so a client stays
    put across refreshes instead of skittering around the map every poll.
    """
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 36000) / 100.0


def normalize_signal(signal_status: Optional[dict]) -> Tuple[Optional[int], Optional[int], bool]:
    """
    Pull (rssi, snr, swapped) out of a client's signalStatus.

    The R1 spec documents an AP firmware bug where RSSI and SNR land in each
    other's fields. RSSI at the AP is always negative dBm and SNR is always a
    positive dB ratio, so a positive `rssi` means the fields are transposed. We
    recover the real RSSI as SNR + noise floor when the noise floor is present,
    and flag it so the caller can report how many readings were repaired.
    """
    if not signal_status:
        return None, None, False

    rssi = signal_status.get("rssi")
    snr = signal_status.get("snr")
    noise_floor = signal_status.get("noiseFloor")

    if isinstance(rssi, (int, float)) and rssi > 0:
        recovered_snr = int(rssi)
        recovered_rssi = None
        if isinstance(noise_floor, (int, float)):
            recovered_rssi = int(recovered_snr + noise_floor)
        elif isinstance(snr, (int, float)) and snr < 0:
            # Fully transposed: the negative value in `snr` is the real RSSI.
            recovered_rssi = int(snr)
        return recovered_rssi, recovered_snr, True

    return (
        int(rssi) if isinstance(rssi, (int, float)) else None,
        int(snr) if isinstance(snr, (int, float)) else None,
        False,
    )


def percentile(values: List[float], pct: float) -> Optional[float]:
    """Linear-interpolated percentile. pct is 0-100."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize_rssi(rssi_values: List[float]) -> Dict[str, Any]:
    """Distribution summary for one AP's client RSSI readings."""
    if not rssi_values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "p10": None,
            "p90": None,
            "tiers": {name: 0 for name, _ in RSSI_TIERS},
        }

    tiers = {name: 0 for name, _ in RSSI_TIERS}
    for value in rssi_values:
        tiers[rssi_tier(value)] += 1

    return {
        "count": len(rssi_values),
        "min": min(rssi_values),
        "max": max(rssi_values),
        "mean": round(sum(rssi_values) / len(rssi_values), 1),
        "median": percentile(rssi_values, 50),
        "p10": percentile(rssi_values, 10),
        "p90": percentile(rssi_values, 90),
        "tiers": tiers,
    }
