"""
Identity normalization for topology correlation.

Every join in this tool goes through here. The inputs disagree about format in
ways that silently break naive matching:

  * switch rows carry `id` == a colon MAC, ports carry `id` == "<dashed-mac>_1-1-24"
  * LLDP chassis IDs from the AP side arrive as "mac c0:c7:0a:3a:3b:d6"
  * port names arrive as "GigabitEthernet1/1/24", LAG members as "1/1/24"
  * LLDP sysName is capped at 32 characters, silently truncating 65% of names

`api/services/wiredwiz/analyze.py:norm_mac` is NOT reused here, deliberately.
It does `.replace("-", ":").replace(".", "")`, which yields two different
canonical forms depending on the input separator -- colon-form for dashed MACs,
bare hex for Cisco-style dotted ones. WiredWiz survives that because all its
inputs come from one R1 field family; Topology's do not.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Set

_HEX12 = re.compile(r"^[0-9a-f]{12}$")
_USP = re.compile(r"(\d+)[/-](\d+)[/-](\d+)\s*$")
_TRAILING_NUM = re.compile(r"(\d+)\s*$")

# LLDP sysName is capped at 32 chars by the standard. Measured on a live
# 112-switch venue: 1956 of 2992 neighbour names were exactly 32 long, with a
# cliff immediately after -- so an exactly-32 name is a truncation, not a
# coincidence. Only 2 of 1915 distinct prefixes matched more than one managed
# device, so prefix resolution is safe as long as ambiguity is checked.
LLDP_NAME_CAP = 32


def norm_mac(value: Any) -> str:
    """
    12 lowercase hex characters, or "" if the value is not a MAC.

    Strict on purpose. A value that will not normalise must never fall through
    as a truthy join key -- that is how a malformed field silently becomes an
    edge between two unrelated devices.

    Handles: aa:bb:cc:dd:ee:ff, AA-BB-CC-DD-EE-FF, aabb.ccdd.eeff, aabbccddeeff,
    and the "mac aa:bb:..." form the AP LLDP endpoint returns.
    """
    text = str(value or "")
    if not text:
        return ""
    stripped = re.sub(r"[^0-9a-fA-F]", "", text).lower()
    return stripped if _HEX12.match(stripped) else ""


def mac_display(mac12: str) -> str:
    """Colon form for the UI. Empty in, empty out."""
    if not mac12 or len(mac12) != 12:
        return ""
    return ":".join(mac12[i:i + 2] for i in range(0, 12, 2))


def oui(mac12: str) -> str:
    """First 6 hex characters -- the vendor prefix."""
    return mac12[:6] if len(mac12) == 12 else ""


def canon_port_ident(value: Any) -> str:
    """
    Canonical "<unit>/<slot>/<port>", or "" when it cannot be parsed.

    Accepts every form seen in the wild: "1/1/24", "1-1-24",
    "GigabitEthernet1/1/24", "TenGigabitEthernet2/1/3", "ethernet 1/1/24",
    "gi1/1/24". A bare number ("24") is treated as unit 1, slot 1 -- which is
    right for the single-unit switches that report that way and wrong for a
    stack, so callers that have a unit id should pass a full identifier.

    Returns "" rather than guessing when there is no number at all; the caller
    then falls back to the MAC path.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    match = _USP.search(text)
    if match:
        return "/".join(str(int(group)) for group in match.groups())
    # A bare port number, e.g. LLDP portId "24" or a LAG member list.
    bare = _TRAILING_NUM.search(text)
    if bare and not re.search(r"[/-]", text):
        return f"1/1/{int(bare.group(1))}"
    return ""


def split_r1_port_id(port_id: Any) -> tuple:
    """
    R1's port `id` -> (mac12, "u/s/p").

    The format is "<dashed-mac>_<u-s-p>", e.g. "10-f0-68-09-0a-72_1-1-1". This
    is also the value the MAC table returns as `switchPortId`, which is how a
    learned MAC joins to a port.

    Note the trap it guards: the port row's own `switchSerial` field is a MAC,
    not a serial, while the MAC table's `switchSerialNumber` is a real serial.
    """
    text = str(port_id or "")
    if "_" not in text:
        return "", ""
    raw_mac, _, raw_port = text.partition("_")
    return norm_mac(raw_mac), canon_port_ident(raw_port)


def name_key(value: Any) -> str:
    """Casefolded, whitespace-collapsed name for comparison."""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def is_truncated_name(value: Any) -> bool:
    """True when a name sits exactly on LLDP's 32-char sysName cap."""
    return len(str(value or "").strip()) == LLDP_NAME_CAP


def parse_vlan_list(value: Any) -> List[int]:
    """
    R1's space-separated VLAN strings -> sorted ints.

    `vlanIds` on a port row looks like "1100 1150 100"; the /topologies edge
    fields use the same shape. Commas appear occasionally, so accept both.
    """
    if value in (None, "", [], {}):
        return []
    if isinstance(value, (list, tuple, set)):
        parts: Iterable[Any] = value
    else:
        parts = re.split(r"[,\s]+", str(value))
    out: Set[int] = set()
    for part in parts:
        try:
            out.add(int(str(part).strip()))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def parse_speed_mbps(value: Any) -> Optional[int]:
    """
    "1 Gb/sec" / "10 Gbps" / "100 Mb/sec" / "1G" -> megabits.

    Used for the speed-corroboration evidence and, later, for spotting the
    narrowest hop on a traced path.
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*(g|m|k)?", text)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    unit = match.group(2)
    if unit == "g":
        return int(number * 1000)
    if unit == "k":
        return int(number / 1000)
    return int(number)


def strip_lldp_prefix(value: Any) -> str:
    """
    The AP neighbour endpoint returns "mac c0:c7:0a:3a:3b:e1" and
    "local 1/1/12" -- a type word, a space, then the value. Everything else
    here expects the value alone.
    """
    text = str(value or "").strip()
    match = re.match(r"^(mac|local|ifname|ifalias|iface|port)\s+(.*)$", text, re.I)
    return match.group(2).strip() if match else text


class Resolution:
    """The outcome of one identity lookup, with its provenance."""

    __slots__ = ("device_id", "via", "ambiguous")

    def __init__(self, device_id: Optional[str] = None, via: str = "unresolved",
                 ambiguous: Optional[List[str]] = None):
        self.device_id = device_id
        self.via = via                       # mac|serial|name|name-prefix|ip|unresolved
        self.ambiguous = ambiguous or []

    def __bool__(self) -> bool:
        return self.device_id is not None

    def __repr__(self) -> str:
        return f"<Resolution {self.device_id} via={self.via} ambiguous={len(self.ambiguous)}>"


class AliasIndex:
    """
    Every way a managed device can be named, mapped back to its device id.

    Built in a fixed order so a run is reproducible: authoritative inventory
    keys first (MAC, serial, name, IP), then port MACs, then the truncated-name
    prefix index. Later registrations never overwrite earlier ones -- the first
    claim on a key wins, and a conflicting second claim is recorded as a
    collision rather than silently replacing it.
    """

    def __init__(self) -> None:
        self.by_mac: Dict[str, str] = {}
        self.by_serial: Dict[str, str] = {}
        self.by_name: Dict[str, str] = {}
        self.by_ip: Dict[str, str] = {}
        self.port_mac_to_port: Dict[str, str] = {}
        self.collisions: List[Dict[str, str]] = []
        self._names: List[tuple] = []        # (name_key, device_id), for prefixes
        self._prefix_index: Optional[Dict[str, List[str]]] = None
        self.ouis: Set[str] = set()

    # ── registration ────────────────────────────────────────────────────────

    def _claim(self, table: Dict[str, str], key: str, device_id: str, kind: str) -> None:
        if not key:
            return
        existing = table.get(key)
        if existing is None:
            table[key] = device_id
        elif existing != device_id:
            self.collisions.append({"kind": kind, "key": key,
                                    "held": existing, "rejected": device_id})

    def add_device(self, device_id: str, *, macs: Iterable[Any] = (),
                   serials: Iterable[Any] = (), names: Iterable[Any] = (),
                   ips: Iterable[Any] = ()) -> None:
        for raw in macs:
            mac = norm_mac(raw)
            if mac:
                self._claim(self.by_mac, mac, device_id, "mac")
                self.ouis.add(oui(mac))
        for raw in serials:
            serial = str(raw or "").strip()
            if serial:
                self._claim(self.by_serial, serial, device_id, "serial")
        for raw in names:
            key = name_key(raw)
            if key:
                self._claim(self.by_name, key, device_id, "name")
                self._names.append((key, device_id))
                self._prefix_index = None
        for raw in ips:
            ip = str(raw or "").strip()
            if ip:
                self._claim(self.by_ip, ip, device_id, "ip")

    def add_port_mac(self, port_mac: Any, port_id: str) -> None:
        """Register a per-port MAC so an LLDP portId can name a specific port."""
        mac = norm_mac(port_mac)
        if mac:
            self.port_mac_to_port.setdefault(mac, port_id)
            self.ouis.add(oui(mac))

    # ── lookup ──────────────────────────────────────────────────────────────

    def _prefixes(self) -> Dict[str, List[str]]:
        if self._prefix_index is None:
            index: Dict[str, List[str]] = {}
            for key, device_id in self._names:
                head = key[:LLDP_NAME_CAP]
                bucket = index.setdefault(head, [])
                if device_id not in bucket:
                    bucket.append(device_id)
            self._prefix_index = index
        return self._prefix_index

    def resolve(self, *, mac: Any = None, serial: Any = None, name: Any = None,
                ip: Any = None) -> Resolution:
        """
        Most specific identifier first. A truncated name resolves ONLY when
        exactly one managed device matches the prefix; an ambiguous prefix
        returns no device and lists the candidates, so the caller can emit a
        contradiction rather than pick one.
        """
        normalized = norm_mac(mac)
        if normalized and normalized in self.by_mac:
            return Resolution(self.by_mac[normalized], "mac")

        serial_text = str(serial or "").strip()
        if serial_text and serial_text in self.by_serial:
            return Resolution(self.by_serial[serial_text], "serial")

        key = name_key(name)
        if key:
            if key in self.by_name:
                return Resolution(self.by_name[key], "name")
            # LLDP often appends a radio suffix to an AP name ("AP-12.5g").
            base = key.rsplit(".", 1)[0]
            if base != key and base in self.by_name:
                return Resolution(self.by_name[base], "name")
            if is_truncated_name(name):
                candidates = self._prefixes().get(key, [])
                if len(candidates) == 1:
                    return Resolution(candidates[0], "name-prefix")
                if len(candidates) > 1:
                    return Resolution(None, "unresolved", candidates)

        ip_text = str(ip or "").strip()
        if ip_text and ip_text in self.by_ip:
            return Resolution(self.by_ip[ip_text], "ip")

        return Resolution()

    def resolve_port(self, port_mac: Any) -> Optional[str]:
        """A port MAC -> that port's id, when we indexed it."""
        return self.port_mac_to_port.get(norm_mac(port_mac))

    def is_foreign_oui(self, mac: Any) -> bool:
        """
        True when a MAC's vendor prefix is one no managed device uses.

        The OUI set is DERIVED from this tenant's own inventory, not hardcoded:
        a foreign OUI on an LLDP neighbour is the strongest cheap signal that a
        port faces something we do not manage -- a firewall, a router, the WAN.
        """
        normalized = norm_mac(mac)
        return bool(normalized) and oui(normalized) not in self.ouis
