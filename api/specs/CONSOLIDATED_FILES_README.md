# Consolidated OpenAPI Files

This directory contains consolidated views of all Ruckus ONE API specifications.

## Generated Files

### 1. full_r1_openapi_spec.json (867 KB) ⭐ OpenAPI 3.0 Standard
**Proper OpenAPI 3.0 compliant specification**

This is a **valid OpenAPI 3.0.1 specification** that merges all 31 Ruckus ONE APIs into a single document.

Contains:
- Standard OpenAPI 3.0.1 structure with `openapi`, `info`, `servers`, `paths`, `tags`, `components`
- All 1,076 unique endpoints (23 duplicates resolved)
- 663 unique paths across all APIs
- 30 tags (one per API)
- Security schemes (Bearer JWT)
- Standard response codes (200, 400, 401, 403, 404, 500)

**Structure:**
```json
{
  "openapi": "3.0.1",
  "info": {
    "title": "Ruckus ONE Complete API",
    "version": "1.0.0"
  },
  "servers": [
    {"url": "https://api.ruckus.cloud", "description": "North America"}
  ],
  "paths": {
    "/venues/{venueId}": {
      "get": {
        "summary": "Retrieve venue",
        "tags": ["Venue Service"],
        "responses": { "200": {...} }
      }
    }
  },
  "tags": [...],
  "components": {
    "securitySchemes": {
      "BearerAuth": {"type": "http", "scheme": "bearer"}
    }
  },
  "security": [{"BearerAuth": []}]
}
```

**Use Cases:**
- ✅ **Import into Swagger UI** - View interactive API docs
- ✅ **Import into Postman** - Test APIs directly
- ✅ **Generate client code** - Use openapi-generator-cli
- ✅ **API validation** - Validate requests/responses
- ✅ **Mock servers** - Generate mock API servers

**Example Usage:**
```bash
# Validate the spec
npx @apidevtools/swagger-cli validate specs/full_r1_openapi_spec.json

# Generate TypeScript client
npx @openapitools/openapi-generator-cli generate \
  -i specs/full_r1_openapi_spec.json \
  -g typescript-axios \
  -o ./generated/r1-client

# View in Swagger UI
# Upload to https://editor.swagger.io
```

---

### 2. full_r1_openapi.json (413 KB) - Custom Format
**Complete endpoint details for all 31 APIs (custom format)**

Contains:
- Full metadata for each API
- Complete endpoint list with method, path, summary, description, tags, operation IDs
- Base URLs and rate limit information
- All 1,099 endpoints across all APIs

**Structure:**
```json
{
  "metadata": {
    "generated_at": "2025-12-02T18:45:08.074475",
    "total_apis": 31,
    "total_endpoints": 1099,
    "source": "Ruckus ONE OpenAPI Specifications",
    "tool": "OpenAPI Manager"
  },
  "apis": {
    "Guest API": {
      "name": "Guest API",
      "version": "1.7.1",
      "url": "https://docs.ruckus.cloud/_bundle/api/guest-1.7.1.yaml",
      "base_url": "https://api.asia.ruckus.cloud",
      "endpoint_count": 21,
      "endpoints": [
        {
          "path": "/templates/portalServiceProfiles/{id}",
          "method": "GET",
          "summary": "Retrieve Portal Service Profile Template",
          "description": "...",
          "operation_id": "getPortalServiceProfile",
          "tags": ["Portal Service Profiles"]
        }
      ]
    }
  }
}
```

**Use Cases:**
- Build automated API documentation
- Generate client SDKs
- Validate API coverage in your implementation
- Search for specific endpoints programmatically
- Build API testing suites

**Example Usage:**
```python
import json

with open('specs/full_r1_openapi.json') as f:
    apis = json.load(f)

# Get all Guest API endpoints
guest_api = apis['apis']['Guest API']
print(f"Guest API has {guest_api['endpoint_count']} endpoints")

# Find all POST endpoints across all APIs
for api_name, api_data in apis['apis'].items():
    post_endpoints = [ep for ep in api_data['endpoints'] if ep['method'] == 'POST']
    if post_endpoints:
        print(f"{api_name}: {len(post_endpoints)} POST endpoints")
```

---

### 3. r1_api_summary.json (11 KB)
**Lightweight summary without full endpoint details**

Contains:
- High-level metadata for each API
- Endpoint counts
- Base URLs and versions
- **No individual endpoint details** (much smaller file)

**Structure:**
```json
{
  "metadata": {
    "generated_at": "2025-12-02T18:45:08.086898",
    "total_apis": 31,
    "total_endpoints": 1099,
    "source": "Ruckus ONE OpenAPI Specifications"
  },
  "apis": [
    {
      "name": "WiFi API",
      "version": "17.3.3.205",
      "url": "https://docs.ruckus.cloud/_bundle/api/wifi-17.3.3.205.yaml",
      "base_url": "https://api.asia.ruckus.cloud",
      "endpoint_count": 432,
      "rate_limit": "## Rate Limit",
      "downloaded_at": "2025-12-02T18:38:18.667475"
    }
  ]
}
```

**Use Cases:**
- Quick API inventory
- Dashboard widgets showing API coverage
- High-level reports
- Faster loading for UI components

**Example Usage:**
```python
import json

with open('specs/r1_api_summary.json') as f:
    summary = json.load(f)

print(f"Total APIs: {summary['metadata']['total_apis']}")
print(f"Total Endpoints: {summary['metadata']['total_endpoints']}")

# Find APIs with most endpoints
sorted_apis = sorted(summary['apis'], key=lambda x: x['endpoint_count'], reverse=True)
print("\nTop 5 APIs by endpoint count:")
for api in sorted_apis[:5]:
    print(f"  {api['name']:40} {api['endpoint_count']:4} endpoints")
```

---

### 4. endpoint_list.md (125 KB)
**Human-readable markdown documentation**

Contains:
- All APIs organized alphabetically
- Endpoints grouped by tags/categories
- Markdown tables for easy reading
- Method, path, and summary for each endpoint

**Use Cases:**
- Browse available APIs
- Share with team members
- Reference during development
- Convert to HTML for documentation site

**Format:**
```markdown
## Guest API (v1.7.1)

**Base URL:** `https://api.asia.ruckus.cloud`

**Endpoint Count:** 21

### Portal Service Profiles

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/templates/portalServiceProfiles/{id}` | Retrieve Portal Service Profile |
| `POST` | `/templates/portalServiceProfiles` | Create Portal Service Profile |
```

---

## Regenerating Files

To regenerate all consolidated files:

```bash
# Inside Docker container
docker exec -it rtools-backend-dev bash
cd /app

# Regenerate consolidated JSON files
python specs/fetch_specs.py consolidate

# Regenerate markdown file
python specs/fetch_specs.py export

# Or do everything at once
python specs/fetch_specs.py all
```

---

## File Locations

```
api/specs/
├── full_r1_openapi_spec.json    # ⭐ OpenAPI 3.0 compliant (867 KB) - USE THIS!
├── full_r1_openapi.json         # Custom format with full details (413 KB)
├── r1_api_summary.json           # Lightweight summary (11 KB)
├── endpoint_list.md              # Human-readable docs (125 KB)
└── cache/                        # Individual analysis files
    ├── *.yaml                    # Downloaded OpenAPI specs
    └── *_analysis.json           # Per-API parsed data
```

## Which File Should I Use?

| Use Case | Recommended File |
|----------|-----------------|
| Import into Swagger UI / Postman | `full_r1_openapi_spec.json` ⭐ |
| Generate client code | `full_r1_openapi_spec.json` ⭐ |
| Programmatic analysis (Python/JS) | `full_r1_openapi.json` |
| Quick API inventory / dashboard | `r1_api_summary.json` |
| Browse/read documentation | `endpoint_list.md` |
| Validate with OpenAPI tools | `full_r1_openapi_spec.json` ⭐ |

---

## Integration Examples

### TypeScript/JavaScript
```typescript
import fullApi from './specs/full_r1_openapi.json';

// Find all endpoints for a specific path pattern
const venueEndpoints = Object.values(fullApi.apis)
  .flatMap(api => api.endpoints)
  .filter(ep => ep.path.includes('/venues'));

console.log(`Found ${venueEndpoints.length} venue-related endpoints`);
```

### Python
```python
import json

# Load summary for quick checks
with open('specs/r1_api_summary.json') as f:
    summary = json.load(f)

# Check if an API exists
api_names = [api['name'] for api in summary['apis']]
if 'DPSK Service' in api_names:
    print("DPSK Service is available!")

# Load full details when needed
with open('specs/full_r1_openapi.json') as f:
    full = json.load(f)
    dpsk = full['apis']['DPSK Service']
    print(f"DPSK has {dpsk['endpoint_count']} endpoints")
```

### FastAPI/Flask
```python
from fastapi import FastAPI
import json

app = FastAPI()

# Load API inventory on startup
with open('specs/r1_api_summary.json') as f:
    api_inventory = json.load(f)

@app.get("/api/inventory")
def get_inventory():
    """Return available Ruckus ONE APIs"""
    return api_inventory

@app.get("/api/endpoints/{api_name}")
def get_api_endpoints(api_name: str):
    """Get all endpoints for a specific API"""
    with open('specs/full_r1_openapi.json') as f:
        full = json.load(f)

    if api_name in full['apis']:
        return full['apis'][api_name]
    else:
        return {"error": "API not found"}
```

---

## Statistics

- **Total APIs:** 31
- **Total Endpoints:** 1,099
- **Largest API:** WiFi API (432 endpoints)
- **Smallest API:** Policy Management API (1 endpoint)
- **File Sizes:**
  - Full JSON: 413 KB
  - Summary JSON: 11 KB
  - Markdown: 125 KB

---

## Version Tracking

These files are automatically regenerated when you run the consolidation tool. The `generated_at` timestamp in each JSON file shows when it was last updated.

To check for new API versions:
```bash
# Re-download specs (force refresh)
python specs/fetch_specs.py download --force

# Re-analyze and consolidate
python specs/fetch_specs.py all
```

---

*Generated by OpenAPI Manager - See [OPENAPI_MANAGER_README.md](OPENAPI_MANAGER_README.md) for more info*
