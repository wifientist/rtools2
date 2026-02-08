# Access Policy Phase Implementation Plan

## Overview

Add a new workflow phase to the cloudpath import that creates adaptive policies linking:
- **Username** (stripped of suffix) + **Per-unit SSID** → **RADIUS Attribute Group** (based on suffix)

Example transformations:
```
Import: "12345_fast" with ssidList: ["101@Sunrise", "102@Sunrise", "Sunrise WiFi"]
                              ↓
1. Parse username → account="12345", suffix="fast"
2. Update identity name → "12345" (strip suffix)
3. Create/find RADIUS group "fast" (10Gbps default if new)
4. Create 2 policies (one per unit SSID, skip site-wide):
   ├─ Policy: Username="12345" AND SSID="101@Sunrise" → Apply "fast"
   └─ Policy: Username="12345" AND SSID="102@Sunrise" → Apply "fast"
5. Add policies to "Sunrise" Policy Set

Import: "67890" with ssidList: ["205@Sunrise"]  (NO SUFFIX)
                              ↓
1. Parse username → account="67890", suffix="gigabit" (DEFAULT)
2. Identity name stays "67890"
3. Create/find RADIUS group "gigabit" (10Gbps)
4. Create policy: Username="67890" AND SSID="205@Sunrise" → Apply "gigabit"
```

---

## API Details (from Production Monitoring)

### 1. RADIUS Attribute Group Creation
```
POST /radiusAttributeGroups
```
**Payload** (NOTE: uses `attributeAssignments`, not `attributes`):
```json
{
  "name": "fast",
  "description": "fast",
  "attributeAssignments": [
    {
      "vendorName": "WISPr",
      "attributeName": "WISPr-Bandwidth-Max-Down",
      "operator": "ADD",
      "attributeValue": "10000000000",
      "dataType": "INTEGER"
    },
    {
      "vendorName": "WISPr",
      "attributeName": "WISPr-Bandwidth-Max-Up",
      "operator": "ADD",
      "attributeValue": "10000000000",
      "dataType": "INTEGER"
    }
  ]
}
```

### 2. Policy Creation (Template 100 = DPSK)
```
POST /policyTemplates/100/policies
```
```json
{
  "name": "108pura",
  "onMatchResponse": "<radius-group-uuid>"
}
```
Response includes `policyType: "DPSK"` and policy ID.

### 3. Policy Conditions
```
POST /policyTemplates/100/policies/{policyId}/conditions
```

**SSID Condition** (templateAttributeId: 1013):
```json
{
  "templateAttributeId": 1013,
  "evaluationRule": {
    "criteriaType": "StringCriteria",
    "regexStringCriteria": "^108@Pura_vida_place$"
  }
}
```

**Username Condition** (templateAttributeId: 1012):
```json
{
  "templateAttributeId": 1012,
  "evaluationRule": {
    "criteriaType": "StringCriteria",
    "regexStringCriteria": "^12345$"
  }
}
```

### 4. Policy Set Assignment
```
PUT /policySets/{policySetId}/prioritizedPolicies/{policyId}
```
```json
{
  "priority": 1
}
```

---

## Magic Numbers / Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `DPSK_POLICY_TEMPLATE_ID` | `100` | Template for DPSK adaptive policies |
| `ATTR_DPSK_USERNAME` | `1012` | Template attribute for username matching |
| `ATTR_WIRELESS_SSID` | `1013` | Template attribute for SSID matching |
| `DEFAULT_BANDWIDTH` | `10000000000` | 10Gbps (essentially "no cap") |
| `DEFAULT_SUFFIX` | `"gigabit"` | Default RADIUS group when no suffix in username |

---

## Implementation Steps

### Step 1: Fix RADIUS Attribute Service
**File**: `api/r1api/services/radius_attributes.py`

Current `create_radius_attribute_group()` uses wrong payload key:
```python
# Current (WRONG)
payload = {"name": name, "attributes": attributes}

# Should be
payload = {"name": name, "attributeAssignments": attributes}
```

Add helper method for bandwidth group creation:
```python
async def create_bandwidth_group(
    self,
    name: str,
    down_bps: int = 10_000_000_000,
    up_bps: int = 10_000_000_000,
    tenant_id: str = None
):
    """Create a WISPr bandwidth limit group."""
    return await self.create_radius_attribute_group(
        name=name,
        attributes=[
            {
                "vendorName": "WISPr",
                "attributeName": "WISPr-Bandwidth-Max-Down",
                "operator": "ADD",
                "attributeValue": str(down_bps),
                "dataType": "INTEGER"
            },
            {
                "vendorName": "WISPr",
                "attributeName": "WISPr-Bandwidth-Max-Up",
                "operator": "ADD",
                "attributeValue": str(up_bps),
                "dataType": "INTEGER"
            }
        ],
        description=name,
        tenant_id=tenant_id
    )
```

### Step 2: Add Policy Condition Creation Method
**File**: `api/r1api/services/policy_sets.py`

Add helper for creating string-match conditions:
```python
async def create_string_condition(
    self,
    template_id: str,
    policy_id: str,
    attribute_id: int,
    regex_pattern: str,
    tenant_id: str = None
):
    """Create a string-matching policy condition."""
    condition_data = {
        "templateAttributeId": attribute_id,
        "evaluationRule": {
            "criteriaType": "StringCriteria",
            "regexStringCriteria": regex_pattern
        }
    }
    return await self.create_policy_condition(
        template_id=template_id,
        policy_id=policy_id,
        condition_data=condition_data,
        tenant_id=tenant_id
    )
```

### Step 3: Create Access Policy Phase (REUSABLE)
**File**: `api/workflow/phases/create_access_policies.py` ← Top-level for reusability

```python
@register_phase("create_access_policies", "Create Access Policies")
class CreateAccessPoliciesPhase(PhaseExecutor):
    """
    Create adaptive policies for DPSK access control.

    REUSABLE across workflows:
    - Cloudpath Import: Parse suffixes from imported identities
    - Per-Unit DPSK: Apply policies to newly created passphrases
    - Any future workflow with DPSK + rate limiting needs

    For each identity:
    1. Parse suffix from username (default: "gigabit")
    2. Strip suffix and rename identity
    3. Validate/create RADIUS attribute group matching suffix
    4. Create policy matching username + unit SSID
    5. Assign policy to property-level policy set
    """
```

**Inputs** (generic for reuse across workflows):
```python
class Inputs(BaseModel):
    # Core data - list of passphrase results with username, identity_id, ssid_list
    created_passphrases: List[PassphraseResult] = Field(default_factory=list)

    # Original passphrases (for ssid_list lookup if not in created_passphrases)
    passphrases: List[Dict[str, Any]] = Field(default_factory=list)

    # Options
    options: Dict[str, Any] = Field(default_factory=dict)
    # Expected options:
    #   enable_access_policies: bool (default False)
    #   policy_set_name: str (default: venue/property name)
    #   default_suffix: str (default: "gigabit")
```

**Outputs**:
- `radius_groups_created`: int
- `radius_groups_existing`: int
- `policies_created`: int
- `policy_set_id`: str
- `policy_results`: List of per-identity results

**Algorithm**:
```
1. If options.enable_access_policies is False → skip phase

2. Parse all passphrases to build policy plan:
   parsed_entries = []
   identities_to_rename = []

   for pp in created_passphrases:
       # Parse username: "12345_fast" → ("12345", "fast")
       #                 "67890"      → ("67890", "gigabit")  DEFAULT
       if "_" in pp.username:
           parts = pp.username.rsplit("_", 1)  # Split from right
           account = parts[0]
           suffix = parts[1]
           # Track for identity rename
           identities_to_rename.append({
               "identity_id": pp.identity_id,
               "old_name": pp.username,
               "new_name": account,
           })
       else:
           account = pp.username
           suffix = "gigabit"  # DEFAULT - no suffix means gigabit

       # Get unit SSIDs only (filter out site-wide)
       # Pattern: "101@PropertyName" matches, "PropertyName WiFi" does not
       unit_ssids = [s for s in pp.ssid_list if UNIT_SSID_PATTERN.match(s)]

       for ssid in unit_ssids:
           # Extract unit number for policy naming: "101@Sunrise" → "101"
           unit_match = UNIT_SSID_PATTERN.match(ssid)
           unit_num = unit_match.group(1) if unit_match else "unknown"

           parsed_entries.append({
               "account": account,
               "suffix": suffix,
               "ssid": ssid,
               "unit_number": unit_num,
               "original_username": pp.username,
               "identity_id": pp.identity_id,
           })

3. Collect unique suffixes → radius_groups_needed
   (Always includes "gigabit" if any entries have default suffix)

4. For each suffix in radius_groups_needed:
   - Query existing groups by name
   - If not found, create with 10Gbps default
   - Store suffix → group_id mapping

5. Get/create property Policy Set (name from options or property name)

6. For each entry in parsed_entries:
   - Policy name: sanitize(f"{entry.account}@{entry.unit_number}")
   - Create policy with onMatchResponse = group_id[entry.suffix]
   - Add username condition: ^{entry.account}$
   - Add SSID condition: ^{regex_escape(entry.ssid)}$
   - Assign to policy set

7. Rename identities (strip suffix from username):
   for identity in identities_to_rename:
       await r1_client.identity.update_identity(
           identity_id=identity.identity_id,
           name=identity.new_name
       )
```

### Step 4: Update Workflow Definitions

The phase is **reusable** - add to any workflow that needs access policies.

#### A. Cloudpath Import (primary use case)
**File**: `api/workflow/workflows/cloudpath_import.py`

Add after `update_identity_descriptions`:

```python
# Phase 5: Create Access Policies (Optional)
Phase(
    id="create_access_policies",
    name="Create Access Policies",
    description=(
        "Create adaptive policies for rate limiting based on "
        "username suffix patterns (e.g., _fast, _gigabit)."
    ),
    executor="create_access_policies",
    depends_on=["create_passphrases"],
    per_unit=False,  # Global phase - dedupe RADIUS groups
    critical=False,
    skip_if="not options.get('enable_access_policies', False)",
    inputs=[
        "created_passphrases", "import_mode", "passphrases", "options"
    ],
    outputs=[
        "radius_groups_created", "policies_created", "policy_set_id"
    ],
    api_calls_per_unit="dynamic",
),
```

#### B. Per-Unit DPSK (future option)
**File**: `api/workflow/workflows/per_unit_dpsk.py`

Could add after `create_passphrases`:
```python
# Phase 5.5: Create Access Policies (Optional)
Phase(
    id="create_access_policies",
    name="Create Access Policies",
    description="Create adaptive policies for rate limiting.",
    executor="create_access_policies",
    depends_on=["create_passphrases"],
    per_unit=False,
    critical=False,
    skip_if="not options.get('enable_access_policies', False)",
    inputs=["created_passphrases", "passphrases", "options"],
    outputs=["radius_groups_created", "policies_created", "policy_set_id"],
    api_calls_per_unit="dynamic",
),
```

### Step 5: Update Validate Phase
**File**: `api/workflow/phases/cloudpath/validate.py`

Add detection of suffix patterns and preview in validation:

```python
# In execute():
# Detect suffix patterns for access policy phase
suffix_patterns = set()
users_with_suffix = 0
users_without_suffix = 0

for pp in passphrases:
    if "_" in pp.name:
        suffix = pp.name.rsplit("_", 1)[1]
        suffix_patterns.add(suffix)
        users_with_suffix += 1
    else:
        users_without_suffix += 1

# Always include gigabit as the default for users without suffix
if users_without_suffix > 0:
    suffix_patterns.add("gigabit")

await self.emit(
    f"Access policy suffixes: {suffix_patterns} "
    f"({users_with_suffix} explicit, {users_without_suffix} defaulting to gigabit)"
)
```

Add to outputs/unit_config:
```python
"suffix_patterns": list(suffix_patterns),
"users_with_suffix": users_with_suffix,
"users_without_suffix": users_without_suffix,
```

---

## UI Considerations (Future)

Add to CloudpathImport.tsx options:
- Checkbox: "Enable Access Policies" (`enable_access_policies`)
- Text input: "Policy Set Name" (defaults to property name)
- Display detected suffixes from validation

---

## Error Handling

1. **RADIUS group creation fails**: Log warning, use "gigabit" as fallback
2. **Policy creation fails**: Log warning, continue with others
3. **No suffix pattern**: Use "gigabit" as default (all identities get a policy)
4. **No unit SSIDs**: Skip identity (only site-wide SSIDs don't need per-user policies)
5. **Identity rename fails**: Log warning, continue (policy still works with old name)

---

## Testing Scenarios

1. **Happy path**: Import with mixed suffixes (_fast, _gigabit), verify policies created
2. **No suffixes**: All plain usernames → default to "gigabit" group, policies still created
3. **Existing RADIUS groups**: Should find and reuse, not duplicate
4. **Mixed SSIDs**: Site-wide + unit-specific → only create policies for unit SSIDs
5. **Single SSID per user**: One policy created
6. **Multiple SSIDs per user**: Multiple policies created (one per unit SSID)
7. **Identity rename**: Verify `12345_fast` becomes `12345` after phase completes
8. **Mixed with/without suffix**: Some have `_fast`, some plain → both get policies

---

## Resolved Decisions

1. **Identity renaming**: ✅ YES - Update identity name to stripped version (remove `_suffix`)
   - `12345_fast` → `12345` after policy is created
   - Suffix is a Cloudpath remnant, not needed once adaptive policy is applied

2. **Default suffix**: ✅ `"gigabit"` - When username has no `_xxx` pattern
   - All identities get a policy, even without explicit suffix
   - "gigabit" = 10Gbps (essentially uncapped)

3. **Policy naming**: ✅ `{account}@{unit_number}` format
   - Extract unit from SSID: `101@Sunrise` → unit `101`
   - Policy name: `12345@101` (sanitized, no special chars)
   - Sanitize function escapes/removes problematic characters

4. **Missing RADIUS group**: ✅ Create with 10Gbps default
   - If "fast" group doesn't exist, create it with 10Gbps up/down
   - All groups use same default bandwidth (true "no cap")

---

## Naming/Sanitization Helper

```python
import re

def sanitize_policy_name(account: str, unit_number: str) -> str:
    """Create a safe policy name from account and unit."""
    # Remove any non-alphanumeric except @ and _
    safe_account = re.sub(r'[^a-zA-Z0-9_]', '', account)
    safe_unit = re.sub(r'[^a-zA-Z0-9]', '', unit_number)
    return f"{safe_account}@{safe_unit}"

def regex_escape_ssid(ssid: str) -> str:
    """Escape SSID for use in regex condition."""
    # Escape regex special characters
    return f"^{re.escape(ssid)}$"
```
