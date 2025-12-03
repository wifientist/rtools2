# Ruckus ONE OpenAPI Specification Manager

This service helps you download, analyze, and track Ruckus ONE OpenAPI specifications to understand what endpoints are available and compare them with your implemented services.

## Overview

The system consists of three main components:

1. **r1_openapi_specs.py** - Registry of all known Ruckus ONE OpenAPI spec URLs
2. **openapi_manager.py** - Core service to download and analyze specs
3. **fetch_specs.py** - CLI tool to interact with the service

## Setup

Since you're using Docker, you'll need to:

1. Rebuild your Docker container to install the new dependencies (aiohttp, PyYAML):
   ```bash
   docker-compose down
   docker-compose build backend
   docker-compose up -d
   ```

## Usage

### Inside Docker Container

```bash
# Enter the backend container
docker exec -it rtools-backend bash

# Show summary of available specs
python specs/fetch_specs.py summary

# Download all OpenAPI specifications
python specs/fetch_specs.py download

# Analyze downloaded specs
python specs/fetch_specs.py analyze

# Export comprehensive endpoint list
python specs/fetch_specs.py export

# Compare with your implementation
python specs/fetch_specs.py compare

# Do everything at once
python specs/fetch_specs.py all
```

### Alternatively, from host (if you want to run without rebuilding)

You can also run the script from your host machine if you have Python 3 and the dependencies:

```bash
cd /home/omni/code/rtools2/api

# Install dependencies (one time)
pip3 install aiohttp PyYAML

# Run commands
python3 specs/fetch_specs.py summary
python3 specs/fetch_specs.py all
```

## What It Does

### 1. Download Specs
Downloads all 31 Ruckus ONE OpenAPI specification files and caches them locally in `specs/cache/`.

### 2. Analyze Specs
Parses each YAML/JSON spec to extract:
- Endpoint count
- All available endpoints (path + method)
- Base URLs
- Rate limiting info
- Endpoint summaries and descriptions
- Tags and operation IDs

### 3. Export Report
Generates a comprehensive markdown file (`endpoint_list.md`) with:
- All APIs organized by service
- Endpoints grouped by tags
- Method, path, and summary for each endpoint

### 4. Compare Implementation
Compares the available APIs against your current implementation to show:
- Total APIs available (31 specs)
- APIs you've implemented
- Coverage percentage
- Missing APIs you haven't implemented yet

## Current Implementation Status

Based on your `r1api/services/` directory, you currently have:

- ✓ **Entitlements** (entitlements.py)
- ✓ **Venues** (venues.py)
- ✓ **MSP Services** (msp.py)
- ✓ **Tenant Management** (tenant.py)
- ✓ **Networks** (networks.py)
- ✓ **APs** (aps.py)
- ✓ **Clients** (clients.py)

This represents approximately **23%** coverage of the available APIs.

## Discovered APIs

The service tracks 31 Ruckus ONE APIs:

### Active/Unknown Status (27 APIs)
- Activities API (0.0.1)
- Admin Enrollment REST API (0.0.1)
- Adaptive Policy Management (0.0.9)
- Certificate Template API (0.0.1)
- Config Template Service API (1.0.0)
- Device Enrollment REST API (0.0.1)
- DPSK Service (0.0.3)
- Events and Alarms API (0.0.3)
- External Auth API (0.0.1)
- File service API (0.2.7)
- Guest API (1.7.1)
- Identity Management (0.0.2)
- MAC Registration API (0.0.1)
- Manage Entitlements API (0.2.0)
- Message Template API (0.0.12)
- MSP Services (0.3.3)
- Policy Management API (0.0.3)
- Property Management REST API (1.0.1)
- RADIUS Attribute Group Management API (1.0.8)
- Resident Portal API (0.0.1)
- RUCKUS Edge API (1.0.3)
- Switch Service API (0.4.0)
- Tenant Management (0.3.0)
- Venue Service API (0.2.8)
- ViewModel service API (1.0.42)
- WiFi API (17.3.3.205)
- Workflow Actions API (0.0.2)
- Workflow Management API (0.0.3)

### Deprecated (3 APIs)
- Entitlement Assignment Endpoints (0.2.0)
- Property Management REST API (1.0.0 - deprecated version)
- Manage Entitlements API (0.2.0 - deprecated version)

## Next Steps

1. **Run the tool** to download and analyze all specs
2. **Review the endpoint_list.md** to see what endpoints are available
3. **Identify high-priority APIs** to implement based on your use cases
4. **Build out service classes** in `r1api/services/` for each API you need

## File Structure

```
api/specs/
├── cache/                              # Downloaded YAML specs and analysis
│   ├── *.yaml                         # Cached OpenAPI specs
│   ├── *_analysis.json                # Parsed endpoint data
│   └── endpoint_list.md               # Generated comprehensive report
├── r1_openapi_specs.py                # Registry of all spec URLs
├── openapi_manager.py                 # Core download/analysis service
├── fetch_specs.py                     # CLI tool
└── OPENAPI_MANAGER_README.md          # This file
```

## Updating Specs

When Ruckus releases new API versions:

1. Update the URLs in `r1_openapi_specs.py`
2. Run `python specs/fetch_specs.py download --force` to re-download
3. Run analysis again to see new endpoints

## Programmatic Usage

You can also import and use the service in your own Python code:

```python
from specs.openapi_manager import OpenAPIManager
import asyncio

async def main():
    manager = OpenAPIManager()

    # Download all specs
    await manager.download_all_specs()

    # Analyze them
    manager.analyze_all_specs()

    # Get endpoint summary
    summary = manager.get_endpoint_summary()
    for api_name, count in summary.items():
        print(f"{api_name}: {count} endpoints")

asyncio.run(main())
```

## Questions?

Check the URLs in [r1_openapi_specs.py](r1_openapi_specs.py) to see all tracked APIs, or review the code in [openapi_manager.py](openapi_manager.py) to understand how it works.
