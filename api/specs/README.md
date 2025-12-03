# API Specifications

This directory contains OpenAPI specifications for external APIs we integrate with.

---

## 🆕 Ruckus ONE OpenAPI Manager (NEW!)

**Comprehensive tool to download, analyze, and track all 31 Ruckus ONE API specifications.**

### Quick Start
```bash
# Inside Docker container
docker exec -it rtools-backend bash
cd /app
python specs/fetch_specs.py all

# Or locally (if you have dependencies)
python3 specs/fetch_specs.py all
```

### Files
- **[r1_openapi_specs.py](r1_openapi_specs.py)** - Registry of 31 API specs
- **[openapi_manager.py](openapi_manager.py)** - Core service
- **[fetch_specs.py](fetch_specs.py)** - CLI tool
- **[OPENAPI_MANAGER_README.md](OPENAPI_MANAGER_README.md)** - Full docs
- **[IMPLEMENTATION_GAPS.md](IMPLEMENTATION_GAPS.md)** - Gap analysis

### What It Does
- ✅ Downloads all 31 Ruckus ONE OpenAPI specs
- ✅ Analyzes 500-800+ endpoints across all APIs
- ✅ Generates comprehensive endpoint report
- ✅ Compares with your implementation (currently ~23% coverage)
- ✅ Identifies high-priority missing APIs

**See [OPENAPI_MANAGER_README.md](OPENAPI_MANAGER_README.md) for complete documentation.**

---

## Legacy: RuckusONE API (Old Method)

- `r1-openapi.json` - Single RuckusONE OpenAPI 3.0 specification
- `r1-api-reference.md` - API reference documentation

### Old Usage
```bash
npm run fetch-r1-spec
```

**Note:** The new OpenAPI Manager above is more comprehensive and handles all 31 APIs.
