"""
Per-identity audit of a Cloudpath import, row by row.

WHAT THIS ANSWERS

The existing /cloudpath-import/audit counts things: how many SSIDs, how many
DPSK pools, how many identity groups. That tells you the scaffolding got
built. It cannot tell you whether resident 4021 can actually get on the
network, which needs five separate objects to line up:

    the identity exists in an identity group
    that group is attached to a DPSK service (pool)
    an adaptive policy is named for the account
    that policy is a member of the property's policy set
    the policy's onMatchResponse points at the right RADIUS attribute group

Any one of those missing and the resident is silently broken -- the import
reports success, the SSID is up, and the phone does not connect. This walks
the file the import was given and reports, per identity, which of those five
are in place.

THE NAME PROBLEM

The import renames identities on the way in. A Cloudpath username carries the
speed tier as a trailing segment:

    file:   4021_ultrafast
    R1:     4021              (create_access_policies strips it)
    policy: 4021              (sanitize_policy_name of the stripped account)
    RADIUS: "ultrafast"       (the stripped segment names the group)

So a file name never matches an imported identity directly. This tries the raw
name first, then the stripped one, and reports which form matched -- because
"matched raw" on a finished import means the rename never ran, which is itself
a finding. Stripping mirrors create_access_policies exactly (rsplit on the last
underscore, unconditionally); anything cleverer would audit a rule the import
does not follow.

WHICH IDENTITIES BELONG TO THIS VENUE

Identity groups are tenant-scoped -- there is no venueId to filter on that can
be trusted. So this follows the serving path instead, which is the same path
traffic takes:

    venue -> its wifi networks (venueApGroups[].venueId)
          -> GET /wifiNetworks/{id}/dpskServices  (the pool link readback)
          -> identity groups whose dpskPoolId is one of those pools
          -> the identities in those groups

That finds what the import actually wired up, whether or not the groups
happen to carry a venueId.

INFORM ONLY

Identities present in R1 but absent from the file are reported as rows with
in_file=False. They are usually left over from a previous roster. This module
deletes nothing and offers no way to; deciding what to remove is a separate
job with a separate blast radius.

COST

Roughly a dozen API calls plus paging, regardless of roster size. Nothing here
is per-identity -- identities, policies, policy-set membership and RADIUS
groups are each fetched in bulk and joined in memory. A 600-resident property
costs the same as a 20-resident one.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from workflow.phases.dpsk_usernames import split_account_suffix
from workflow.phases.create_access_policies import (
    ATTR_DPSK_USERNAME,
    ATTR_WIRELESS_SSID,
    DPSK_POLICY_TEMPLATE_ID,
    DEFAULT_SUFFIX,
    regex_pattern_for_value,
    sanitize_policy_name,
)

logger = logging.getLogger(__name__)

# Paging guards. R1 caps page size well below these; the limits exist so a
# misbehaving endpoint cannot spin this endpoint forever.
MAX_PAGES = 200
IDENTITY_PAGE_SIZE = 500
POLICY_PAGE_SIZE = 500

# How many policy sets to inspect when the caller does not name one. Each
# costs a membership read, and a tenant has a handful, not hundreds.
MAX_POLICY_SETS = 50

# A DPSK policy carries exactly two conditions: the username and the SSID.
# conditionsCount comes back in the policy LIST for free, so a policy missing
# one -- or carrying a stale third -- is detectable without reading anything.
DPSK_EXPECTED_CONDITIONS = 2

# Conditions are per-policy with no bulk read, so the deep check costs one
# call per resident. Capped so a 600-resident property opens a sane number of
# sockets rather than all of them at once.
CONDITION_FETCH_CONCURRENCY = 8


# ==================== Request / response models ====================


class FileIdentity(BaseModel):
    """One DPSK entry as it appears in the uploaded Cloudpath export."""
    name: str
    ssids: List[str] = Field(default_factory=list)


class IdentityAuditRequest(BaseModel):
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="Venue to audit")
    identities: List[FileIdentity] = Field(
        ..., description="Identities parsed from the uploaded file"
    )
    policy_set_name: Optional[str] = Field(
        None,
        description="Policy set to check membership against. When omitted, "
                    "every policy set in the tenant is consulted and the row "
                    "reports which one holds the policy.",
    )
    default_suffix: str = Field(
        DEFAULT_SUFFIX,
        description="Speed tier assumed for usernames with no trailing _suffix",
    )
    evaluate_policies: bool = Field(
        False,
        description="Ask R1 which policy each identity actually lands on, via "
                    "POST /policySets/{id}/evaluationReports. The only check "
                    "that accounts for priority order, so it catches a "
                    "different policy winning ahead of the right one. One "
                    "extra API call per identity, and it needs a policy set "
                    "named in policy_set_name.",
    )
    verify_conditions: bool = Field(
        False,
        description="Read each matched policy's conditions to check what it "
                    "actually matches on. One extra API call per resident, so "
                    "it is opt-in; without it the policy column only says a "
                    "policy with the right name exists.",
    )


class IdentityAuditRow(BaseModel):
    """One identity, and which of the five links are in place."""
    # Columns 1 and 2. The two names ARE the existence check: a name in
    # username_json means the file has this resident, a name in username_r1
    # means R1 does. A separate "in file" tick would only restate the first
    # one, and neither tick tells you the thing that actually matters here --
    # that the two names differ, and how.
    username_json: Optional[str] = None   # as the Cloudpath export spells it
    username_r1: Optional[str] = None     # as RuckusONE spells it
    account: str                          # the processed/stripped form
    suffix: Optional[str] = None          # the trailing segment, or the default

    # Kept for filtering and totals, not for a column of its own.
    in_file: bool

    # Column 3
    in_identity_group: bool = False
    identity_group_name: Optional[str] = None
    identity_id: Optional[str] = None
    matched_as: Optional[str] = None    # "exact" | "processed" | None

    # Column 3
    in_dpsk_service: bool = False
    dpsk_service_name: Optional[str] = None

    # Column 4
    in_adaptive_policy: bool = False
    policy_name: Optional[str] = None
    policy_id: Optional[str] = None
    policy_in_set: bool = False
    policy_set_name: Optional[str] = None

    # What the policy actually matches on. Populated only when the caller
    # asked to verify conditions; None everywhere means "not checked", which
    # is not the same as "checked and empty".
    conditions_count: Optional[int] = None
    conditions_checked: bool = False
    policy_username_regex: Optional[str] = None
    policy_ssid_regex: Optional[str] = None
    policy_username_matches: Optional[bool] = None
    policy_ssid_in_file: Optional[bool] = None

    # What R1 says actually happens, priority order included. None means the
    # evaluation was not run for this row, which is not the same as no match.
    evaluated: bool = False
    evaluated_matched: Optional[bool] = None
    evaluated_policy_name: Optional[str] = None
    evaluated_radius_group: Optional[str] = None
    evaluated_ssid: Optional[str] = None
    evaluated_wrong_policy: Optional[bool] = None

    # Column 5
    radius_group_name: Optional[str] = None
    radius_group_expected: Optional[str] = None
    radius_group_matches: Optional[bool] = None

    # Column 6
    has_description: bool = False

    # Anything worth the reader's attention on this row.
    issues: List[str] = Field(default_factory=list)


class IdentityAuditResponse(BaseModel):
    venue_id: str
    venue_name: str
    policy_sets_checked: List[str] = Field(default_factory=list)
    identity_groups_scanned: List[str] = Field(default_factory=list)
    dpsk_services_scanned: List[str] = Field(default_factory=list)
    totals: Dict[str, int] = Field(default_factory=dict)
    rows: List[IdentityAuditRow] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ==================== Name handling ====================


# split_account_suffix comes from workflow.phases.dpsk_usernames -- the same
# function the import itself splits with. The audit exists to say whether the
# import did its job, so it has to relate file names to R1 names by the
# import's own rule. A private copy that drifted would report disagreement
# between two of our own functions as a resident being broken.


# ==================== R1 collection ====================


async def _dpsk_pool_ids_for_venue(r1_client, tenant_id: str, venue_id: str) -> Tuple[Set[str], List[str]]:
    """
    The DPSK pools actually serving this venue, via its networks.

    The link is write-only from the network side -- a wifiNetworks/query row
    never carries a dpskService field however many you ask for -- so each
    candidate network has to be asked directly.

    Returns (pool ids, warnings).
    """
    warnings: List[str] = []

    networks_response = await r1_client.networks.get_wifi_networks(
        tenant_id, venue_id=venue_id
    )
    all_networks = networks_response.get('data', []) if isinstance(networks_response, dict) else []

    venue_network_ids: List[str] = []
    for network in all_networks:
        for vag in network.get('venueApGroups') or []:
            if vag.get('venueId') == venue_id:
                if network.get('id'):
                    venue_network_ids.append(network['id'])
                break

    pool_ids: Set[str] = set()
    for network_id in venue_network_ids:
        try:
            linked = await r1_client.networks.get_dpsk_services_on_network(
                network_id=network_id, tenant_id=tenant_id
            )
            rows = linked.get('data', linked.get('content', [])) if isinstance(linked, dict) else (linked or [])
            for row in rows:
                if isinstance(row, dict) and row.get('id'):
                    pool_ids.add(row['id'])
        except Exception as e:
            # One unreadable network should not blank the whole audit; say so
            # rather than reporting its residents as missing.
            logger.warning(f"Could not read DPSK services on network {network_id}: {e}")
            warnings.append(
                f"Could not read the DPSK link on one network ({network_id}); "
                f"identities served only by it may show as missing"
            )

    logger.info(
        f"Venue {venue_id}: {len(venue_network_ids)} networks, "
        f"{len(pool_ids)} DPSK pools serving it"
    )
    return pool_ids, warnings


async def _identities_for_pools(
    r1_client, tenant_id: str, pool_ids: Set[str]
) -> Tuple[Dict[str, Dict[str, Any]], List[str], List[str]]:
    """
    Every identity in every group attached to one of these pools.

    Returns (name -> record, group names scanned, pool names scanned).
    """
    ig_response = await r1_client.identity.query_identity_groups(
        tenant_id=tenant_id, page=0, size=1000
    )
    groups = (
        ig_response.get('content', ig_response.get('data', []))
        if isinstance(ig_response, dict) else (ig_response or [])
    )
    groups = [g for g in groups if g.get('dpskPoolId') in pool_ids]

    pool_names: Dict[str, str] = {}
    for pool_id in {g.get('dpskPoolId') for g in groups if g.get('dpskPoolId')}:
        try:
            pool = await r1_client.dpsk.get_dpsk_pool(pool_id, tenant_id)
            pool_names[pool_id] = pool.get('name') or pool_id
        except Exception as e:
            logger.warning(f"Could not read DPSK pool {pool_id}: {e}")
            pool_names[pool_id] = pool_id

    by_name: Dict[str, Dict[str, Any]] = {}
    group_names: List[str] = []

    for group in groups:
        group_id = group.get('id')
        group_name = group.get('name') or group_id
        pool_id = group.get('dpskPoolId')
        if not group_id:
            continue
        group_names.append(group_name)

        page = 0
        while page < MAX_PAGES:
            response = await r1_client.identity.get_identities_in_group(
                group_id=group_id, tenant_id=tenant_id,
                page=page, size=IDENTITY_PAGE_SIZE,
            )
            items = (
                response.get('content', response.get('data', []))
                if isinstance(response, dict) else (response or [])
            )
            for identity in items:
                name = identity.get('name')
                if not name:
                    continue
                by_name[name] = {
                    'identity_id': identity.get('id'),
                    'name': name,
                    'description': identity.get('description') or '',
                    'identity_group_name': group_name,
                    'dpsk_pool_id': pool_id,
                    'dpsk_service_name': pool_names.get(pool_id),
                }

            is_last = response.get('last') if isinstance(response, dict) else None
            if is_last is True or not items:
                break
            if is_last is None and len(items) < IDENTITY_PAGE_SIZE:
                break
            page += 1

    logger.info(
        f"Scanned {len(group_names)} identity groups, {len(by_name)} identities"
    )
    return by_name, group_names, sorted(set(pool_names.values()))


async def _policies_by_name(r1_client, tenant_id: str) -> Dict[str, Dict[str, Any]]:
    """
    Every DPSK-template policy, keyed by name.

    Policies are tenant-scoped and named for the account alone, so this is the
    whole tenant -- there is nothing to scope it to the venue by.
    """
    by_name: Dict[str, Dict[str, Any]] = {}
    page = 0
    while page < MAX_PAGES:
        response = await r1_client.policy_sets.query_template_policies(
            template_id=DPSK_POLICY_TEMPLATE_ID,
            tenant_id=tenant_id,
            page=page,
            limit=POLICY_PAGE_SIZE,
        )
        items = (
            response.get('content', response.get('data', []))
            if isinstance(response, dict) else (response or [])
        )
        for policy in items:
            name = policy.get('name')
            if name:
                by_name[name] = policy
        if len(items) < POLICY_PAGE_SIZE:
            break
        page += 1

    logger.info(f"Found {len(by_name)} policies in template {DPSK_POLICY_TEMPLATE_ID}")
    return by_name


async def _conditions_for_policies(
    r1_client, tenant_id: str, policy_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """
    policy id -> {username_regex, ssid_regex, other, error}.

    THE DEEP CHECK. Everything else in this audit asks whether a policy with
    the right NAME exists and what RADIUS group it points at. Neither says
    what the policy actually MATCHES ON, and a policy named "8081081081"
    whose username condition still reads "^8081081080$" from a mistyped
    earlier run looks perfectly healthy by name.

    There is no bulk read -- the policy list carries only conditionsCount --
    so this is one call per resident, which is why it is opt-in. Bounded
    concurrency, and one policy's failure is recorded on that policy rather
    than sinking the audit.
    """
    semaphore = asyncio.Semaphore(CONDITION_FETCH_CONCURRENCY)
    results: Dict[str, Dict[str, Any]] = {}

    async def fetch(policy_id: str) -> None:
        async with semaphore:
            try:
                response = await r1_client.policy_sets.get_policy_conditions(
                    template_id=DPSK_POLICY_TEMPLATE_ID,
                    policy_id=policy_id,
                    tenant_id=tenant_id,
                )
            except Exception as e:
                logger.warning(f"Could not read conditions for policy {policy_id}: {e}")
                results[policy_id] = {"error": str(e)}
                return

            items = (
                response.get('content', response.get('data', []))
                if isinstance(response, dict) else (response or [])
            )
            record: Dict[str, Any] = {
                "username_regex": None, "ssid_regex": None,
                "other": [], "error": None,
            }
            for cond in items:
                if not isinstance(cond, dict):
                    continue
                rule = cond.get('evaluationRule') or {}
                pattern = rule.get('regexStringCriteria')
                attr = cond.get('templateAttributeId')
                if attr == ATTR_DPSK_USERNAME:
                    record["username_regex"] = pattern
                elif attr == ATTR_WIRELESS_SSID:
                    record["ssid_regex"] = pattern
                else:
                    record["other"].append(str(attr))
            results[policy_id] = record

    await asyncio.gather(*(fetch(pid) for pid in policy_ids))
    logger.info(f"Read conditions for {len(results)} policies")
    return results


# VERIFIED against a live tenant 2026-09-20. matchName is the attribute TEXT,
# not the numeric id, and GET /policyTemplates/100/attributes does not even
# return 1013 -- these come from the templateAttribute embedded in real policy
# conditions. The `type` discriminator has no mapping in the spec; the literal
# R1 accepts is "StringEvaluation" (the CONDITIONS side spells its own
# discriminator "StringCriteria", which is a different thing).
EVAL_MATCH_USERNAME = "Dpsk_Username"
EVAL_MATCH_SSID = "SSID"
EVAL_STRING_TYPE = "StringEvaluation"


async def _evaluate_identities(
    r1_client, tenant_id: str, policy_set_id: str,
    cases: List[Tuple[str, str]],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    Ask R1 which policy each (username, ssid) actually lands on.

    THE ONLY AUTHORITATIVE CHECK IN THIS MODULE. Everything else infers: a
    policy with the right name exists, its regex looks right. None of that
    accounts for PRIORITY -- policies in a set are evaluated in order and the
    first match wins, so a higher-priority policy with a looser regex can
    swallow a resident whose own policy is perfectly correct. This asks R1 to
    run the evaluation and report what it actually returns.

    A non-match comes back as HTTP 200 with wasMatched false, not an error, so
    the flag is what matters rather than the status.

    One call per case, same as the conditions read, hence opt-in.
    """
    semaphore = asyncio.Semaphore(CONDITION_FETCH_CONCURRENCY)
    results: Dict[Tuple[str, str], Dict[str, Any]] = {}

    async def run(case: Tuple[str, str]) -> None:
        username, ssid = case
        payload = {
            "policySetId": policy_set_id,
            "evaluationCriteria": [
                {"matchName": EVAL_MATCH_USERNAME,
                 "value": {"type": EVAL_STRING_TYPE, "stringValue": username}},
                {"matchName": EVAL_MATCH_SSID,
                 "value": {"type": EVAL_STRING_TYPE, "stringValue": ssid}},
            ],
        }
        kwargs = {"override_tenant_id": tenant_id} if tenant_id else {}
        async with semaphore:
            try:
                response = await asyncio.to_thread(
                    r1_client.policy_sets.client.post,
                    f"/policySets/{policy_set_id}/evaluationReports",
                    payload=payload, **kwargs,
                )
                status = getattr(response, "status_code", 0)
                body = r1_client.policy_sets.client.safe_json(response)
            except Exception as e:
                logger.warning(f"Policy evaluation failed for {username}: {e}")
                results[case] = {"error": str(e)}
                return

            if not (isinstance(status, int) and 200 <= status < 300):
                message = body.get("message") if isinstance(body, dict) else None
                results[case] = {"error": message or f"HTTP {status}"}
                return

            body = body if isinstance(body, dict) else {}
            results[case] = {
                "matched": bool(body.get("wasMatched")),
                "policy_name": body.get("policyName"),
                "policy_id": body.get("policyId"),
                "radius_id": body.get("onMatchResponse"),
                "error": None,
            }

    await asyncio.gather(*(run(c) for c in cases))
    logger.info(f"Evaluated {len(results)} identity/SSID pairs against R1")
    return results


async def _policy_set_membership(
    r1_client, tenant_id: str, policy_set_name: Optional[str]
) -> Tuple[Dict[str, str], List[str], List[str]]:
    """
    policy id -> the name of the set holding it.

    When the caller names a set, only that one is consulted, and a policy
    outside it reads as not in the set -- which is the honest answer, since a
    policy that is not in the property's set does nothing at the property.
    When no set is named, every set is consulted so the row can say where the
    policy actually lives.

    Returns (policy id -> set name, set names checked, warnings).
    """
    warnings: List[str] = []

    sets_response = await r1_client.policy_sets.query_policy_sets(
        tenant_id=tenant_id, page=0, limit=MAX_POLICY_SETS
    )
    all_sets = (
        sets_response.get('content', sets_response.get('data', []))
        if isinstance(sets_response, dict) else (sets_response or [])
    )

    if policy_set_name:
        wanted = [
            s for s in all_sets
            if (s.get('name') or '').lower() == policy_set_name.lower()
        ]
        if not wanted:
            warnings.append(
                f"No policy set named '{policy_set_name}' exists in this "
                f"tenant, so no policy can be a member of it yet"
            )
        all_sets = wanted

    membership: Dict[str, str] = {}
    checked: List[str] = []
    # name -> id, so the evaluation pass can address the set directly.
    checked_ids: Dict[str, str] = {}

    for pset in all_sets:
        set_id = pset.get('id')
        set_name = pset.get('name') or set_id
        if not set_id:
            continue
        checked.append(set_name)
        checked_ids[set_name] = set_id
        try:
            prioritized = await r1_client.policy_sets.get_prioritized_policies(
                policy_set_id=set_id, tenant_id=tenant_id
            )
            rows = (
                prioritized.get('content', prioritized.get('data', []))
                if isinstance(prioritized, dict) else (prioritized or [])
            )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                policy_id = row.get('policyId') or row.get('id')
                if policy_id:
                    membership.setdefault(policy_id, set_name)
        except Exception as e:
            logger.warning(f"Could not read members of policy set {set_name}: {e}")
            warnings.append(
                f"Could not read the members of policy set '{set_name}'; "
                f"policy-set membership is unknown for this run"
            )

    return membership, checked, checked_ids, warnings


async def _radius_group_names(r1_client, tenant_id: str) -> Dict[str, str]:
    """RADIUS attribute group id -> name, for resolving onMatchResponse."""
    try:
        response = await r1_client.radius_attributes.get_radius_attribute_groups(
            tenant_id=tenant_id
        )
        groups = (
            response.get('content', response.get('data', []))
            if isinstance(response, dict) else (response or [])
        )
        return {g['id']: g.get('name') or g['id'] for g in groups if g.get('id')}
    except Exception as e:
        logger.warning(f"Could not list RADIUS attribute groups: {e}")
        return {}


# ==================== The audit ====================


def _build_row(
    *,
    username_json: Optional[str],
    account: str,
    suffix: Optional[str],
    expected_suffix: Optional[str],
    in_file: bool,
    identity: Optional[Dict[str, Any]],
    matched_as: Optional[str],
    policies: Dict[str, Dict[str, Any]],
    membership: Dict[str, str],
    radius_names: Dict[str, str],
    scoped_set_name: Optional[str],
    conditions: Optional[Dict[str, Dict[str, Any]]] = None,
    file_ssids: Optional[Set[str]] = None,
    evaluation: Optional[Dict[str, Any]] = None,
    evaluated_ssid: Optional[str] = None,
) -> IdentityAuditRow:
    """Join one identity against everything collected, and name what is wrong."""
    row = IdentityAuditRow(
        username_json=username_json,
        account=account,
        suffix=suffix,
        in_file=in_file,
    )

    if identity:
        row.in_identity_group = True
        row.identity_group_name = identity.get('identity_group_name')
        row.identity_id = identity.get('identity_id')
        row.username_r1 = identity.get('name')
        row.matched_as = matched_as
        row.has_description = bool(identity.get('description'))
        # The group was reached BY its pool, so a group implies a service.
        row.in_dpsk_service = bool(identity.get('dpsk_service_name'))
        row.dpsk_service_name = identity.get('dpsk_service_name')

    policy_name = sanitize_policy_name(account)
    policy = policies.get(policy_name)
    if policy:
        row.in_adaptive_policy = True
        row.policy_name = policy_name
        row.policy_id = policy.get('id')
        holder = membership.get(policy.get('id')) if policy.get('id') else None
        row.policy_in_set = bool(holder)
        row.policy_set_name = holder

        radius_id = policy.get('onMatchResponse')
        if radius_id:
            row.radius_group_name = radius_names.get(radius_id, radius_id)

        # Free: the policy LIST carries conditionsCount, so a policy missing
        # its SSID condition (or carrying a stale extra) shows up without
        # reading anything. It cannot tell us the regexes are RIGHT, only
        # that the right number of them exist.
        count = policy.get('conditionsCount')
        if isinstance(count, int):
            row.conditions_count = count

        # Opt-in: what the policy actually matches on.
        detail = (conditions or {}).get(policy.get('id'))
        if detail and not detail.get('error'):
            row.conditions_checked = True
            row.policy_username_regex = detail.get('username_regex')
            row.policy_ssid_regex = detail.get('ssid_regex')
            if row.policy_username_regex is not None:
                # The import anchors the ACCOUNT, not the file username.
                row.policy_username_matches = (
                    row.policy_username_regex == regex_pattern_for_value(account)
                )
            if row.policy_ssid_regex is not None and file_ssids is not None:
                # An anchored "^101@Prop$" unwrapped back to the SSID, then
                # checked against the SSIDs this file actually mentions. A
                # policy pointing at a network the roster never names is
                # matching nothing a resident will ever associate to.
                bare = row.policy_ssid_regex
                if bare.startswith('^') and bare.endswith('$'):
                    bare = bare[1:-1].replace('\\', '')
                row.policy_ssid_in_file = bare in file_ssids

    # ---------------------------------------------------------------------
    # Only the FILE can say which RADIUS group a resident should be on, and
    # only for a resident the file actually lists.
    #
    # This used to compare against `suffix`, which for an identity absent
    # from the file falls through to the default tier -- so an R1-only
    # identity named "4099" was reported as "RADIUS group is 'superfast',
    # expected 'gigabit'". Nothing expected gigabit. The default tier is what
    # the import assigns to a FILE username carrying no suffix; read against
    # an identity that was never in the file it is not a weak signal, it is a
    # fabricated one. Unknown is the honest answer, and it leaves the cell
    # showing the group's name with no verdict attached.
    # ---------------------------------------------------------------------
    # ---- what R1 says actually happens ----
    if evaluation and not evaluation.get("error"):
        row.evaluated = True
        row.evaluated_ssid = evaluated_ssid
        row.evaluated_matched = evaluation.get("matched")
        row.evaluated_policy_name = evaluation.get("policy_name")
        radius_id = evaluation.get("radius_id")
        if radius_id:
            row.evaluated_radius_group = radius_names.get(radius_id, radius_id)
        if row.evaluated_matched and row.evaluated_policy_name:
            row.evaluated_wrong_policy = (
                row.evaluated_policy_name != policy_name
            )

    row.radius_group_expected = expected_suffix
    if row.radius_group_name and expected_suffix:
        row.radius_group_matches = (
            row.radius_group_name.lower() == expected_suffix.lower()
        )

    # ---- findings, in the order they break a resident's connection ----
    if in_file and not row.in_identity_group:
        row.issues.append("No identity in any identity group serving this venue")
    if row.in_identity_group and not row.in_dpsk_service:
        row.issues.append("Identity group is not attached to a DPSK service")
    if matched_as == "exact" and suffix and "_" in (username_json or ""):
        # The identity still carries its speed tier, so the rename step never
        # completed -- and the policy is named for the stripped account, so it
        # will not match this username at RADIUS time.
        row.issues.append(
            f"Identity still named '{username_json}'; the import should have "
            f"renamed it to '{account}'"
        )
    if in_file and not row.in_adaptive_policy:
        row.issues.append(f"No adaptive policy named '{policy_name}'")
    if row.in_adaptive_policy and not row.policy_in_set:
        row.issues.append(
            f"Policy '{policy_name}' exists but is in no policy set"
            + (f" (expected '{scoped_set_name}')" if scoped_set_name else "")
            + ", so it has no effect"
        )
    if (
        row.in_adaptive_policy
        and row.conditions_count is not None
        and row.conditions_count != DPSK_EXPECTED_CONDITIONS
    ):
        row.issues.append(
            f"Policy has {row.conditions_count} condition(s), expected "
            f"{DPSK_EXPECTED_CONDITIONS} (a DPSK username and an SSID)"
        )
    if row.conditions_checked and row.policy_username_matches is False:
        row.issues.append(
            f"Policy matches username {row.policy_username_regex}, not "
            f"{regex_pattern_for_value(account)} — it will never fire for "
            f"this resident"
        )
    if row.conditions_checked and row.policy_ssid_in_file is False:
        row.issues.append(
            f"Policy matches SSID {row.policy_ssid_regex}, which is not an "
            f"SSID this file mentions"
        )
    if row.in_adaptive_policy and not row.radius_group_name:
        row.issues.append("Policy has no RADIUS attribute group")
    elif row.radius_group_matches is False:
        row.issues.append(
            f"RADIUS group is '{row.radius_group_name}', expected "
            f"'{expected_suffix}' from the username in the file"
        )
    if row.evaluated and row.evaluated_matched is False:
        row.issues.append(
            f"R1 evaluates this identity on {row.evaluated_ssid!r} and NO "
            f"policy matches — it gets no adaptive policy at all"
        )
    if row.evaluated_wrong_policy:
        row.issues.append(
            f"R1 matches policy '{row.evaluated_policy_name}', not "
            f"'{policy_name}' — a higher-priority policy wins"
        )
    if (
        row.evaluated_radius_group and expected_suffix
        and row.evaluated_radius_group.lower() != expected_suffix.lower()
    ):
        row.issues.append(
            f"R1 actually applies RADIUS group "
            f"'{row.evaluated_radius_group}', not '{expected_suffix}'"
        )
    if row.in_identity_group and not row.has_description:
        row.issues.append("No description set")

    return row


async def run_identity_audit(
    r1_client, request: IdentityAuditRequest
) -> IdentityAuditResponse:
    """Walk the uploaded roster against what R1 actually holds."""
    tenant_id = request.tenant_id
    venue_id = request.venue_id

    venue = await r1_client.venues.get_venue(tenant_id, venue_id)
    venue_name = venue.get('name', 'Unknown') if isinstance(venue, dict) else 'Unknown'

    pool_ids, warnings = await _dpsk_pool_ids_for_venue(r1_client, tenant_id, venue_id)

    if not pool_ids:
        warnings.append(
            "No DPSK service is linked to any network at this venue, so no "
            "identity can be serving it yet"
        )

    identities, group_names, pool_names = await _identities_for_pools(
        r1_client, tenant_id, pool_ids
    )
    policies = await _policies_by_name(r1_client, tenant_id)
    membership, sets_checked, set_ids, set_warnings = await _policy_set_membership(
        r1_client, tenant_id, request.policy_set_name
    )
    warnings.extend(set_warnings)
    radius_names = await _radius_group_names(r1_client, tenant_id)

    # Every SSID the roster mentions, so a policy's SSID condition can be
    # checked against networks that actually exist in this import.
    file_ssids: Set[str] = {
        ssid for entry in request.identities for ssid in (entry.ssids or [])
    }

    # The deep check, if asked for: only policies we will actually report on,
    # so an unrelated tenant-wide policy costs nothing.
    conditions: Dict[str, Dict[str, Any]] = {}
    if request.verify_conditions:
        wanted: Set[str] = set()
        for entry in request.identities:
            name = (entry.name or '').strip()
            if not name:
                continue
            account, _suffix = split_account_suffix(name, request.default_suffix)
            policy = policies.get(sanitize_policy_name(account))
            if policy and policy.get('id'):
                wanted.add(policy['id'])
        for r1_name in identities:
            account, _suffix = split_account_suffix(r1_name, request.default_suffix)
            policy = policies.get(sanitize_policy_name(account))
            if policy and policy.get('id'):
                wanted.add(policy['id'])
        if wanted:
            conditions = await _conditions_for_policies(
                r1_client, tenant_id, sorted(wanted)
            )
            unreadable = sum(1 for v in conditions.values() if v.get('error'))
            if unreadable:
                warnings.append(
                    f"Could not read the conditions of {unreadable} policy(s); "
                    f"those rows show no regex rather than a wrong one"
                )

    # ---- Tier 2: ask R1 what actually happens ----
    evaluations: Dict[Tuple[str, str], Dict[str, Any]] = {}
    eval_ssid_for: Dict[str, str] = {}
    if request.evaluate_policies:
        target_set = None
        if request.policy_set_name:
            target_set = set_ids.get(request.policy_set_name) or next(
                (sid for name, sid in set_ids.items()
                 if name.lower() == request.policy_set_name.lower()), None
            )
        if not target_set:
            warnings.append(
                "Policy evaluation needs one policy set to evaluate against; "
                "set the policy set name on the import form and re-run the "
                "audit. Skipped."
            )
        else:
            cases: Set[Tuple[str, str]] = set()
            for entry in request.identities:
                name = (entry.name or "").strip()
                if not name:
                    continue
                account, _sfx = split_account_suffix(name, request.default_suffix)
                # The username R1 will see at auth time is the identity's
                # name, which the import strips -- so evaluate the account
                # when an identity exists under it, and the raw name when the
                # rename never happened.
                identity = identities.get(name) or identities.get(account)
                auth_name = (identity or {}).get("name") or account
                # A policy's SSID condition names a UNIT network, so evaluate
                # against one of those rather than a property-wide SSID.
                unit = next(
                    (x for x in (entry.ssids or []) if "@" in x and not x.startswith("@")),
                    None,
                )
                if not unit:
                    continue
                eval_ssid_for[name] = unit
                cases.add((auth_name, unit))
            if cases:
                evaluations = await _evaluate_identities(
                    r1_client, tenant_id, target_set, sorted(cases)
                )
                failed = sum(1 for v in evaluations.values() if v.get("error"))
                if failed:
                    warnings.append(
                        f"R1 could not evaluate {failed} identity(s); those "
                        f"rows show no verdict rather than a wrong one"
                    )

    rows: List[IdentityAuditRow] = []
    consumed: Set[str] = set()

    # ---- rows from the file, in file order ----
    for entry in request.identities:
        username = (entry.name or '').strip()
        if not username:
            continue

        account, suffix = split_account_suffix(username, request.default_suffix)

        # Raw first: an identity still carrying its suffix means the rename
        # never ran, which the row should say rather than hide.
        identity = identities.get(username)
        matched_as = "exact" if identity else None
        if not identity:
            identity = identities.get(account)
            matched_as = "processed" if identity else None

        if identity and identity.get('name'):
            consumed.add(identity['name'])

        rows.append(_build_row(
            username_json=username,
            account=account,
            suffix=suffix,
            # The file lists this resident, so its username -- explicit tier
            # or the default for one without -- is a real expectation.
            expected_suffix=suffix,
            in_file=True,
            identity=identity,
            matched_as=matched_as,
            policies=policies,
            membership=membership,
            radius_names=radius_names,
            scoped_set_name=request.policy_set_name,
            conditions=conditions,
            file_ssids=file_ssids,
            evaluation=evaluations.get(
                ((identity or {}).get("name") or account, eval_ssid_for.get(username, "")),
            ),
            evaluated_ssid=eval_ssid_for.get(username),
        ))

    # ---- identities R1 holds that the file does not mention ----
    # Reported, never acted on. Usually a previous roster; sometimes a
    # hand-added resident that belongs there.
    extras = 0
    for name, identity in identities.items():
        if name in consumed:
            continue
        extras += 1
        # What R1 stores, read by the same rule -- but note this name may
        # have no tier in it at all (a processed "4099"), in which case the
        # split hands back the DEFAULT, which describes nothing about this
        # identity. Hence expected_suffix=None below: the file is the only
        # thing entitled to say what a resident should have been given, and
        # it does not mention this one.
        account, suffix = split_account_suffix(name, request.default_suffix)
        if "_" not in name:
            suffix = None
        row = _build_row(
            username_json=None,
            account=account,
            suffix=suffix,
            expected_suffix=None,
            in_file=False,
            identity=identity,
            matched_as="exact",
            policies=policies,
            membership=membership,
            radius_names=radius_names,
            scoped_set_name=request.policy_set_name,
            conditions=conditions,
            file_ssids=file_ssids,
        )
        row.issues.insert(0, "Present in R1 but not in the uploaded file")
        rows.append(row)

    in_file_rows = [r for r in rows if r.in_file]
    totals = {
        "rows": len(rows),
        "in_file": len(in_file_rows),
        "in_r1_only": extras,
        "matched": sum(1 for r in in_file_rows if r.in_identity_group),
        "matched_processed": sum(1 for r in in_file_rows if r.matched_as == "processed"),
        "matched_exact": sum(1 for r in in_file_rows if r.matched_as == "exact"),
        "missing_identity": sum(1 for r in in_file_rows if not r.in_identity_group),
        "in_dpsk_service": sum(1 for r in in_file_rows if r.in_dpsk_service),
        "with_policy": sum(1 for r in in_file_rows if r.in_adaptive_policy),
        "policy_in_set": sum(1 for r in in_file_rows if r.policy_in_set),
        "radius_mismatch": sum(1 for r in in_file_rows if r.radius_group_matches is False),
        "with_description": sum(1 for r in in_file_rows if r.has_description),
        "clean": sum(1 for r in in_file_rows if not r.issues),
        "conditions_checked": sum(1 for r in rows if r.conditions_checked),
        "evaluated": sum(1 for r in rows if r.evaluated),
        "evaluated_no_match": sum(1 for r in rows if r.evaluated_matched is False),
        "evaluated_wrong_policy": sum(1 for r in rows if r.evaluated_wrong_policy),
        "wrong_condition_count": sum(
            1 for r in rows
            if r.conditions_count is not None
            and r.in_adaptive_policy
            and r.conditions_count != DPSK_EXPECTED_CONDITIONS
        ),
        "policy_username_mismatch": sum(
            1 for r in rows if r.policy_username_matches is False
        ),
        "policy_ssid_unknown": sum(
            1 for r in rows if r.policy_ssid_in_file is False
        ),
    }

    return IdentityAuditResponse(
        venue_id=venue_id,
        venue_name=venue_name,
        policy_sets_checked=sets_checked,
        identity_groups_scanned=sorted(set(group_names)),
        dpsk_services_scanned=pool_names,
        totals=totals,
        rows=rows,
        warnings=warnings,
    )
