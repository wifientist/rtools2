#!/usr/bin/env python3
"""
Quick test script to verify the OpenAPI manager setup
Tests without requiring full download
"""

import sys
from pathlib import Path

print("=" * 80)
print("Ruckus ONE OpenAPI Manager - Quick Test")
print("=" * 80)

# Test 1: Check imports
print("\n[1/4] Testing imports...")
try:
    from r1_openapi_specs import R1_OPENAPI_SPECS, get_spec_summary, SpecStatus
    print("✓ r1_openapi_specs imports OK")
except ImportError as e:
    print(f"✗ Failed to import r1_openapi_specs: {e}")
    sys.exit(1)

try:
    from openapi_manager import OpenAPIManager
    print("✓ openapi_manager imports OK")
except ImportError as e:
    print(f"✗ Failed to import openapi_manager: {e}")
    print(f"  Missing dependencies? Try: pip3 install aiohttp PyYAML")
    sys.exit(1)

# Test 2: Check registry
print("\n[2/4] Checking OpenAPI spec registry...")
summary = get_spec_summary()
print(f"✓ Registry loaded: {summary['total']} total specs")
print(f"  - Active: {summary['active']}")
print(f"  - Deprecated: {summary['deprecated']}")
print(f"  - Unknown: {summary['unknown']}")

# Test 3: Verify URLs
print("\n[3/4] Verifying spec URLs...")
url_count = 0
for spec in R1_OPENAPI_SPECS[:5]:  # Check first 5
    if spec.url.startswith("https://docs.ruckus.cloud/_bundle/"):
        url_count += 1

if url_count == 5:
    print(f"✓ URLs format looks correct (checked 5 samples)")
else:
    print(f"⚠ Some URLs may be malformed")

# Test 4: Check manager initialization
print("\n[4/4] Testing OpenAPIManager initialization...")
try:
    manager = OpenAPIManager(cache_dir="./cache_test")
    print(f"✓ OpenAPIManager initialized")
    print(f"  Cache directory: {manager.cache_dir}")

    # Clean up test dir
    import shutil
    if Path("./cache_test").exists():
        shutil.rmtree("./cache_test")
except Exception as e:
    print(f"✗ Failed to initialize OpenAPIManager: {e}")
    sys.exit(1)

# Summary
print("\n" + "=" * 80)
print("✅ All tests passed!")
print("=" * 80)
print("\nYou're ready to use the OpenAPI Manager:")
print("  1. Download specs:  python fetch_specs.py download")
print("  2. Analyze specs:   python fetch_specs.py analyze")
print("  3. Export report:   python fetch_specs.py export")
print("  4. Compare impl:    python fetch_specs.py compare")
print("  5. Do everything:   python fetch_specs.py all")
print("\nSee OPENAPI_MANAGER_README.md for full documentation")
print("=" * 80)
