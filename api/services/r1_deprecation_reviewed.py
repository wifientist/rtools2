"""
R1 calls the deprecation audit flags from the spec, checked by hand and found
fine as we call them. Each entry says what was checked, when, and against
which spec.

An entry drops the call to "info". A live `ruckus-deprecated` header or a
405/410 from R1 overrides it at once, and a newer spec than `spec` raises it
back to "medium" until someone re-checks and updates the entry.

Key: (method, path) as the audit writes it, ids as {x}.
"""

_IDENTITY_V1 = (
    "Spec: 'This method will be removed... application/json is currently tied to "
    "vnd.ruckus.v1+json which will be moved to v1.1 on 08/31/2026'. The 'method' is "
    "the v1 media type: live, v1 already answers 406 while the plain application/json "
    "we send is accepted with no ruckus-deprecated header (v1.1 is response-only; as a "
    "request body it is a 415). Our code already handles the async 202 + requestId."
)

_V11_PINNED = ("R1 names v1.1 as current (ruckus-current-version) while plain JSON got v1. Every reader "
               "now asks for v1.1: SZ migration and Config Rules already did; PISR, Venue Checkup and "
               "venues.get_ap_radio_settings since 2026-11-11.")

REVIEWED = {
    ("get", "/venues/{x}/apRadioSettings"): {"date": "2026-11-11", "spec": "RUCKUS_One_Consolidated_API_11112026.json", "note": _V11_PINNED},
    ("get", "/venues/{x}/apMeshSettings"): {"date": "2026-11-11", "spec": "RUCKUS_One_Consolidated_API_11112026.json", "note": _V11_PINNED},
    ("get", "/venues/{x}/aaaSettings"): {"date": "2026-11-11", "spec": "RUCKUS_One_Consolidated_API_11112026.json", "note": _V11_PINNED},
    ("post", "/identityGroups"): {"date": "2026-11-11", "spec": "RUCKUS_One_Consolidated_API_11112026.json", "note": _IDENTITY_V1},
    ("delete", "/identityGroups/{x}"): {"date": "2026-11-11", "spec": "RUCKUS_One_Consolidated_API_11112026.json", "note": _IDENTITY_V1},
    ("post", "/identityGroups/{x}/identities"): {"date": "2026-11-11", "spec": "RUCKUS_One_Consolidated_API_11112026.json", "note": _IDENTITY_V1},
    ("delete", "/identityGroups/{x}/identities"): {"date": "2026-11-11", "spec": "RUCKUS_One_Consolidated_API_11112026.json", "note": _IDENTITY_V1},
    ("patch", "/identityGroups/{x}/identities/{x}"): {"date": "2026-11-11", "spec": "RUCKUS_One_Consolidated_API_11112026.json", "note": _IDENTITY_V1},
}


# Calls our code no longer makes, and when the replacement went in. The audit
# drops them to "info" while nothing calls them; R1 flagging one on a live
# response AFTER `since` raises it again, since something still calls it.
RETIRED = {
    ("get", "/venues"): {"since": "2026-11-11T15:30:00+00:00", "replaced_by": "POST /venues/query"},
    ("post", "/mspecs/query"): {"since": "2026-11-11T15:30:00+00:00", "replaced_by": "POST /tenants/query"},
    ("post", "/techpartners/mspecs/query"): {"since": "2026-11-11T15:30:00+00:00", "replaced_by": "POST /tenants/query (installer/integrator filter)"},
    # Batch 2, 2026-11-11.
    ("get", "/radiusServerProfiles"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "POST /radiusServerProfiles/query (ids) + GET /radiusServerProfiles/{id} v1.1 (rows with secrets)"},
    ("get", "/venues/apGroups"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "POST /venues/apGroups/query"},
    ("get", "/venues/{x}/apGroups"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "POST /venues/apGroups/query (venueId filter)"},
    ("get", "/entitlements"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "POST /entitlements/query"},
    ("get", "/mspEntitlements"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "POST /entitlements/query"},
    ("get", "/mspLabels"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "GET /brandings"},
    ("get", "/mspCustomers/{x}/admins"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "GET /tenants/{id}/admins"},
    ("get", "/policySets"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "POST /policySets/query (0-indexed)"},
    ("get", "/policyTemplates/policies"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "POST /policyTemplates/policies/query (0-indexed)"},
    # Dead code deleted 2026-11-11 (nothing called it).
    ("get", "/apconfig"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "nothing (deleted; never a real route)"},
    ("post", "/deviceProvisions/aps"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "nothing (deleted, unused)"},
    ("post", "/venues/{x}/aps/{x}"): {"since": "2026-11-11T15:45:00+00:00", "replaced_by": "nothing (deleted, unused)"},
    # Batch 3, 2026-11-11.
    ("post", "/networkActivations"): {"since": "2026-11-11T17:11:00+00:00", "replaced_by": "PUT venue wifiNetworks + apGroups + settings (activate_ssid_for_ap_group_direct)"},
    ("get", "/venues/{x}/apModelLanPortSettings"): {"since": "2026-11-11T17:11:00+00:00", "replaced_by": "lanPortSpecificSettings + lanPorts/{p}/settings + ethernetPortProfiles (sz_migration.lan_ports)"},
    ("put", "/venues/{x}/apModelLanPortSettings"): {"since": "2026-11-11T17:11:00+00:00", "replaced_by": "per-model/per-port writes + Ethernet Port Profiles (sz_migration.lan_ports)"},
    ("get", "/networks/{x}"): {"since": "2026-11-11T17:11:00+00:00", "replaced_by": "profile queries' wifiNetworkIds (Config Rules legacy flags)"},
    ("get", "/venues/{x}/rogueApSettings"): {"since": "2026-11-11T17:11:00+00:00", "replaced_by": "GET /venues/{id}/roguePolicySettings"},
    ("get", "/venues/{x}/dhcpPools"): {"since": "2026-11-11T17:11:00+00:00", "replaced_by": "DHCP service profile + wifiDhcpPoolUsages (PISR)"},
    ("get", "/venues/{x}/snmpAgentSettings"): {"since": "2026-11-11T17:11:00+00:00", "replaced_by": "POST /snmpAgentProfiles/query (venueIds)"},
    ("get", "/venues/{x}/syslogSettings"): {"since": "2026-11-11T17:11:00+00:00", "replaced_by": "POST /syslogServerProfiles/query (venueIds)"},
    ("get", "/venues/{x}/wifiSettings"): {"since": "2026-11-11T17:11:00+00:00", "replaced_by": "nothing (Venue Checkup row removed; Radio row covers it)"},
}
