"""
What the APs say about their own uplinks.

Three sources, of very different value:

  `ap.selfreport.switchserial` -- the AP row's `switchSerialNumber`. Present on
  2619 of 2788 APs and resolving to a managed switch 99.9% of the time, so it is
  excellent COVERAGE. But it is measured NOT to be an independent observation:
  across 2657 APs it agreed with the far switch's own LLDP 2657 times and
  disagreed zero times, and it is a near-perfect subset of switch LLDP (3 APs
  self-report without LLDP backing; 101 are seen by LLDP without self-reporting).
  A genuinely separate look would disagree sometimes and have symmetric gaps.
  So its channel is `r1-derived`: it corroborates, and it never promotes a link
  to `confirmed`.

  `mesh.uplink` -- an AP's mesh parent, from `uplink[].upMac` and `meshRole`.
  A real AP-side observation of a wireless link.

  `lldp.ap.tlv` -- the AP's own LLDP neighbour cache, the ONLY genuinely
  independent AP-side channel. It is also almost always empty: 11 of 12 sampled
  APs returned HTTP 400 `WIFI-10498 "No detected neighbor data."` and the one
  that answered was 18 days stale. Warming it needs a PATCH, which is a write.
  So it is opt-in (`deep=true`) and expected to contribute little -- but when it
  does answer, it is the one thing that can raise an AP link to `confirmed`.
"""

from typing import Iterator, Optional

from ..model import Endpoint, Evidence
from ..normalize import (canon_port_ident, mac_display, norm_mac,
                         strip_lldp_prefix)
from . import Claim, source


@source("ap.selfreport.switchserial", "AP's reported uplink switch",
        proves="An AP names the switch it is attached to",
        reads="POST /venues/aps/query: switchSerialNumber, poePort, poePortStatus",
        caveats="Broad coverage (2619/2788 APs, 99.9% resolving) but NOT an "
                "independent observation -- it agreed with switch LLDP 2657/2657 "
                "times and never disagreed, so R1 is restating that LLDP. It "
                "corroborates and cannot promote a link to 'confirmed'. Note "
                "`switchName`/`switchPort` come back EMPTY, so the switch-side "
                "port is not known from here.")
def ap_self_report(bundle, index) -> Iterator[Claim]:
    """An AP reports the serial of the switch feeding it."""
    # Switches elsewhere in the tenant, so an AP fed from a venue the user did
    # not select still gets an uplink drawn. On the probe venue this was the
    # entire explanation for the 3 online APs that otherwise had no link at all:
    # all three are cabled to switches in QuadrantC. Dropping them would have
    # been the map quietly omitting real cabling.
    out_of_scope = {}
    for row in getattr(bundle, "all_switches", None) or []:
        row_serial = str(row.get("serialNumber") or "")
        if row_serial and not index.aliases.resolve(serial=row_serial):
            out_of_scope[row_serial] = row

    for device in index.devices.values():
        if device.kind != "ap":
            continue
        serial = device.attrs.get("switchSerialNumber")
        if not serial:
            continue
        poe_port = device.attrs.get("poePort")
        poe_status = device.attrs.get("poePortStatus")

        resolution = index.aliases.resolve(serial=str(serial))
        if not resolution or resolution.device_id == device.id:
            elsewhere = out_of_scope.get(str(serial))
            if elsewhere is None:
                continue
            yield _out_of_scope_claim(device, elsewhere, serial, poe_port, poe_status)
            continue
        switch = index.device(resolution.device_id)
        if switch is None or switch.kind not in ("switch", "stack"):
            continue

        yield Claim(
            # The AP end names its OWN lan port; the switch end is device-level,
            # because `switchPort` is empty on every row.
            a=Endpoint(device_id=device.id, resolved_via="self",
                       ident=f"eth{poe_port}" if poe_port not in (None, "") else None,
                       discovered_as={"poePort": poe_port,
                                      "poePortStatus": poe_status}),
            b=Endpoint(device_id=switch.id, resolved_via="serial",
                       discovered_as={"switchSerialNumber": serial}),
            kind="ethernet", channel="r1-derived", observer=device.id,
            evidence=[Evidence(
                source="ap.selfreport.switchserial", kind="support",
                claim=f"{device.display_name} reports its uplink switch as "
                      f"{switch.display_name} (serial {serial})"
                      + (f", with its own port {poe_port} {poe_status}."
                         if poe_status else "."),
                a=f"dev:{device.id}", b=f"dev:{switch.id}",
                fields={"switchSerialNumber": serial, "apPoePort": poe_port,
                        "poePortStatus": poe_status,
                        "note": "R1 does not report the switch-side port here."},
            )])


def _out_of_scope_claim(device, switch_row, serial, poe_port, poe_status) -> Claim:
    """
    An AP fed by a managed switch in a venue outside this scope.

    Drawn as an external node rather than omitted: "this AP's uplink leaves the
    venue you selected" is useful information, and silence looks identical to a
    broken AP.
    """
    mac = norm_mac(switch_row.get("switchMac") or switch_row.get("id"))
    venue = switch_row.get("venueName")
    return Claim(
        a=Endpoint(device_id=device.id, resolved_via="self",
                   ident=f"eth{poe_port}" if poe_port not in (None, "") else None,
                   discovered_as={"poePort": poe_port, "poePortStatus": poe_status}),
        b=Endpoint(device_id=f"ext:mac:{mac}" if mac else f"ext:serial:{serial}",
                   resolved_via="out-of-scope",
                   discovered_as={"name": switch_row.get("name"),
                                  "mac": mac, "serial": serial,
                                  "venueName": venue, "outOfScope": True}),
        kind="ethernet", channel="r1-derived", observer=device.id,
        evidence=[Evidence(
            source="ap.selfreport.switchserial", kind="support",
            claim=f"{device.display_name} reports its uplink switch as "
                  f"{switch_row.get('name')} (serial {serial}), which is in "
                  f"'{venue}' -- outside the venues you selected. Add that venue "
                  f"to see this uplink and everything behind it.",
            a=f"dev:{device.id}", b=f"dev:ext:mac:{mac}",
            fields={"switchSerialNumber": serial, "switchName": switch_row.get("name"),
                    "venueName": venue, "outOfScope": True,
                    "apPoePort": poe_port, "poePortStatus": poe_status},
        )])


@source("mesh.uplink", "AP mesh parent",
        proves="An AP reports a wireless uplink to another AP",
        reads="POST /venues/aps/query: meshRole, uplink[].upMac, hops",
        caveats="Only meaningful where mesh is enabled. The probe venue had mesh "
                "disabled on all 994 APs.")
def mesh_uplinks(bundle, index) -> Iterator[Claim]:
    """An AP in a mesh names the AP it uplinks through."""
    by_serial = {str(row.get("serialNumber")): row for row in
                 (getattr(bundle, "aps", None) or []) if row.get("serialNumber")}

    for device in index.devices.values():
        if device.kind != "ap":
            continue
        role = str(device.attrs.get("meshRole") or "")
        if not role or role.upper() in ("DISABLED", "ROOT", ""):
            continue
        row = by_serial.get(device.serial) or {}
        uplinks = row.get("uplink") or []
        if not isinstance(uplinks, list):
            continue
        for entry in uplinks:
            if not isinstance(entry, dict):
                continue
            parent_mac = norm_mac(entry.get("upMac"))
            if not parent_mac:
                continue
            resolution = index.aliases.resolve(mac=parent_mac)
            if not resolution or resolution.device_id == device.id:
                continue
            parent = index.device(resolution.device_id)
            yield Claim(
                a=Endpoint(device_id=device.id, resolved_via="self",
                           discovered_as={"meshRole": role,
                                          "hops": row.get("hops")}),
                b=Endpoint(device_id=resolution.device_id, resolved_via="mac",
                           discovered_as={"upMac": mac_display(parent_mac)}),
                kind="mesh", channel="ap-mesh", observer=device.id,
                evidence=[Evidence(
                    source="mesh.uplink", kind="support",
                    claim=f"{device.display_name} is a mesh {role.lower()} and "
                          f"reports its uplink through "
                          f"{parent.display_name if parent else mac_display(parent_mac)}"
                          + (f", {row.get('hops')} hop(s) from root."
                             if row.get("hops") not in (None, "") else "."),
                    a=f"dev:{device.id}", b=f"dev:{resolution.device_id}",
                    fields={"meshRole": role, "hops": row.get("hops"),
                            "upMac": mac_display(parent_mac),
                            "rssi": entry.get("rssi"), "type": entry.get("type")},
                )])


@source("lldp.ap.tlv", "AP's own LLDP neighbour cache",
        proves="An AP independently reports what it sees on its wired port",
        reads="POST /venues/{v}/aps/{serial}/neighbors/query (deep scan only)",
        caveats="The ONLY independent AP-side channel, and almost always empty: "
                "11 of 12 sampled APs returned 400 WIFI-10498 'No detected "
                "neighbor data', and the one that answered was 18 days stale. "
                "Warming the cache needs a PATCH, which is a write and out of "
                "scope. Opt-in via deep=true; expect little.",
        enabled_by_default=True)
def ap_lldp(bundle, index) -> Iterator[Claim]:
    """
    LLDP as the AP itself saw it.

    `lldpChassisID` and `lldpPortID` arrive with a literal "mac " prefix, and
    `lldpPortDesc` ("GigabitEthernet1/1/12") is the only field in the whole API
    that names the far port from the AP's side -- which is exactly what makes
    this the one source able to confirm an AP link.
    """
    rows_by_serial = getattr(bundle, "ap_lldp", None) or {}
    for serial, rows in rows_by_serial.items():
        ap_device_id = f"ap:{serial}"
        ap_device = index.device(ap_device_id)
        if ap_device is None:
            continue
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            chassis = strip_lldp_prefix(row.get("lldpChassisID"))
            far_serial = row.get("neighborSerialNumber")
            sys_name = row.get("lldpSysName")
            resolution = index.aliases.resolve(mac=chassis, serial=far_serial,
                                               name=sys_name)
            if not resolution or resolution.device_id == ap_device_id:
                continue
            switch = index.device(resolution.device_id)

            # lldpPortDesc names the far port directly; lldpPortID is a port MAC.
            far_ident = canon_port_ident(row.get("lldpPortDesc"))
            far_port_id = None
            if far_ident:
                candidate = f"{resolution.device_id}#{far_ident}"
                if index.port(candidate):
                    far_port_id = candidate
            if far_port_id is None:
                port_mac = strip_lldp_prefix(row.get("lldpPortID"))
                by_mac = index.aliases.resolve_port(port_mac)
                far_port = index.port(by_mac)
                if far_port and far_port.device_id == resolution.device_id:
                    far_port_id = by_mac
                    far_ident = far_port.ident

            yield Claim(
                a=Endpoint(device_id=ap_device_id, resolved_via="self",
                           ident=str(row.get("lldpInterface") or "") or None,
                           discovered_as={"lldpInterface": row.get("lldpInterface"),
                                          "detectedTime": row.get("detectedTime")}),
                b=Endpoint(device_id=resolution.device_id, port_id=far_port_id,
                           ident=far_ident or None,
                           resolved_via=resolution.via,
                           discovered_as={"lldpSysName": sys_name,
                                          "lldpChassisID": chassis,
                                          "lldpPortDesc": row.get("lldpPortDesc")}),
                kind="ethernet", channel="ap-lldp", observer=ap_device_id,
                evidence=[Evidence(
                    source="lldp.ap.tlv", kind="support",
                    claim=f"{ap_device.display_name} reports, from its own LLDP on "
                          f"{row.get('lldpInterface') or 'its uplink'}, a neighbour "
                          f"'{sys_name}'"
                          + (f" port {far_ident}." if far_ident else "."),
                    a=f"dev:{ap_device_id}", b=far_port_id or f"dev:{resolution.device_id}",
                    fields={"lldpSysName": sys_name, "lldpChassisID": chassis,
                            "lldpPortID": row.get("lldpPortID"),
                            "lldpPortDesc": row.get("lldpPortDesc"),
                            "lldpMgmtIP": row.get("lldpMgmtIP"),
                            "neighborManaged": row.get("neighborManaged"),
                            "neighborSerialNumber": far_serial,
                            "detectedTime": row.get("detectedTime"),
                            "resolvedVia": resolution.via},
                    observed_at=str(row.get("detectedTime") or ""),
                )])
