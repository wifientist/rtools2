#!/usr/bin/env python3
"""
CLI tool to fetch and analyze Ruckus ONE OpenAPI specifications

Usage:
    python fetch_specs.py download           # Download all specs
    python fetch_specs.py analyze            # Analyze downloaded specs
    python fetch_specs.py export             # Export endpoint list
    python fetch_specs.py consolidate        # Create consolidated JSON files
    python fetch_specs.py compare            # Compare with our implementation
    python fetch_specs.py all                # Do everything
"""

import sys
import asyncio
from pathlib import Path

# Handle both direct execution and module import
try:
    from openapi_manager import OpenAPIManager
    from r1_openapi_specs import get_spec_summary, SpecStatus
except ImportError:
    # Add parent directory to path so we can import from specs
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from specs.openapi_manager import OpenAPIManager
    from specs.r1_openapi_specs import get_spec_summary, SpecStatus


def print_spec_summary():
    """Print summary of available specs"""
    summary = get_spec_summary()
    print("\n📋 Ruckus ONE OpenAPI Specifications Registry")
    print("=" * 80)
    print(f"Total specs: {summary['total']}")
    print(f"  Active: {summary['active']}")
    print(f"  Deprecated: {summary['deprecated']}")
    print(f"  Unknown status: {summary['unknown']}")
    print("=" * 80)


async def download_specs(force: bool = False):
    """Download all OpenAPI specs"""
    manager = OpenAPIManager()
    await manager.download_all_specs(force=force)


def analyze_specs():
    """Analyze all downloaded specs"""
    manager = OpenAPIManager()
    analyses = manager.analyze_all_specs()

    # Print endpoint counts
    print("\n📊 Endpoint Counts by API:")
    print("=" * 80)
    summary = manager.get_endpoint_summary()

    # Sort by endpoint count (descending)
    sorted_apis = sorted(summary.items(), key=lambda x: x[1], reverse=True)

    for api_name, count in sorted_apis:
        print(f"{api_name:50} {count:4} endpoints")

    total = sum(summary.values())
    print("=" * 80)
    print(f"{'TOTAL':50} {total:4} endpoints")


def export_endpoints():
    """Export comprehensive endpoint list"""
    manager = OpenAPIManager()

    # First analyze (uses cached analysis if available)
    manager.analyze_all_specs()

    # Export to markdown in specs folder (not cache)
    manager.export_endpoint_list()  # Uses default path

    print(f"\n✅ Endpoint list exported to: specs/endpoint_list.md")
    print(f"   You can now review all {sum(manager.get_endpoint_summary().values())} endpoints")


def consolidate_json():
    """Create consolidated JSON files"""
    try:
        from consolidate_specs import consolidate_analyses, create_summary_json, create_openapi_spec
    except ImportError:
        # Try with different import path
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from consolidate_specs import consolidate_analyses, create_summary_json, create_openapi_spec

    # Create all consolidated files
    consolidate_analyses()
    create_summary_json()
    create_openapi_spec()


def compare_implementation():
    """Compare with our current implementation"""
    manager = OpenAPIManager()

    # First analyze
    manager.analyze_all_specs()

    # List of what we've currently implemented
    # Based on the r1api/services directory
    implemented = [
        "Manage Entitlements API",
        "Venue Service API",
        "MSP Services",
        "Tenant Management",
        "WiFi API",  # Partial - via aps and networks services
    ]

    comparison = manager.compare_with_implementation(implemented)

    print("\n🔍 Implementation Coverage Analysis")
    print("=" * 80)
    print(f"Total APIs available: {comparison['total_apis']}")
    print(f"APIs implemented: {comparison['implemented']}")
    print(f"Coverage: {comparison['coverage_percent']:.1f}%")
    print("\n")

    print("📝 Implemented APIs:")
    for api in implemented:
        print(f"  ✓ {api}")

    print(f"\n❌ Missing APIs ({len(comparison['missing'])}):")
    for api in sorted(comparison['missing']):
        print(f"  • {api}")

    if comparison['extra']:
        print(f"\n⚠️  Extra implementations (not in spec registry):")
        for api in comparison['extra']:
            print(f"  • {api}")


async def do_all():
    """Download, analyze, and export everything"""
    print_spec_summary()

    print("\n" + "=" * 80)
    print("STEP 1: Downloading specifications")
    print("=" * 80)
    await download_specs(force=False)

    print("\n" + "=" * 80)
    print("STEP 2: Analyzing specifications")
    print("=" * 80)
    analyze_specs()

    print("\n" + "=" * 80)
    print("STEP 3: Exporting endpoint list")
    print("=" * 80)
    export_endpoints()

    print("\n" + "=" * 80)
    print("STEP 4: Consolidating JSON files")
    print("=" * 80)
    consolidate_json()

    print("\n" + "=" * 80)
    print("STEP 5: Comparing with implementation")
    print("=" * 80)
    compare_implementation()

    print("\n✅ All done!")


def main():
    """Main CLI entry point"""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "download":
        force = "--force" in sys.argv
        print_spec_summary()
        asyncio.run(download_specs(force=force))

    elif command == "analyze":
        analyze_specs()

    elif command == "export":
        export_endpoints()

    elif command == "consolidate":
        consolidate_json()

    elif command == "compare":
        compare_implementation()

    elif command == "all":
        asyncio.run(do_all())

    elif command == "summary":
        print_spec_summary()

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
