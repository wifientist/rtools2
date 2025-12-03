#!/usr/bin/env python3
"""
Consolidate all individual OpenAPI analysis JSON files into a single comprehensive JSON file
"""

import json
from pathlib import Path
from datetime import datetime

# Handle both direct execution and module import
try:
    from openapi_manager import OpenAPIManager
    from r1_openapi_specs import R1_OPENAPI_SPECS
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from openapi_manager import OpenAPIManager
    from r1_openapi_specs import R1_OPENAPI_SPECS


def consolidate_analyses(cache_dir: str = "./specs/cache", output_file: str = "full_r1_openapi.json"):
    """
    Consolidate all individual analysis JSON files into one comprehensive file

    Args:
        cache_dir: Directory containing the analysis JSON files
        output_file: Name of the output consolidated JSON file
    """
    cache_path = Path(cache_dir)
    # Put output in specs folder, not cache folder
    output_path = cache_path.parent / output_file

    print(f"🔍 Looking for analysis files in: {cache_path}")
    print("=" * 80)

    # Find all analysis JSON files
    analysis_files = list(cache_path.glob("*_analysis.json"))

    if not analysis_files:
        print("❌ No analysis files found!")
        print("   Run 'python fetch_specs.py analyze' first to generate analyses")
        return None

    print(f"✓ Found {len(analysis_files)} analysis files")

    # Load all analyses
    consolidated = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "total_apis": 0,
            "total_endpoints": 0,
            "source": "Ruckus ONE OpenAPI Specifications",
            "tool": "OpenAPI Manager"
        },
        "apis": {}
    }

    total_endpoints = 0
    loaded_count = 0

    for analysis_file in sorted(analysis_files):
        try:
            with open(analysis_file, 'r') as f:
                analysis = json.load(f)

            api_name = analysis.get("name", "Unknown")
            endpoint_count = analysis.get("endpoint_count", 0)

            # Add to consolidated structure
            consolidated["apis"][api_name] = analysis

            total_endpoints += endpoint_count
            loaded_count += 1

            print(f"  ✓ Loaded: {api_name} ({endpoint_count} endpoints)")

        except Exception as e:
            print(f"  ✗ Error loading {analysis_file.name}: {e}")

    # Update metadata
    consolidated["metadata"]["total_apis"] = loaded_count
    consolidated["metadata"]["total_endpoints"] = total_endpoints

    # Write consolidated file
    print(f"\n📝 Writing consolidated file...")
    with open(output_path, 'w') as f:
        json.dump(consolidated, f, indent=2)

    file_size = output_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)

    print("=" * 80)
    print(f"✅ Consolidated {loaded_count} APIs into: {output_path}")
    print(f"   Total endpoints: {total_endpoints}")
    print(f"   File size: {file_size_mb:.2f} MB")
    print("=" * 80)

    return str(output_path)


def create_summary_json(cache_dir: str = "./specs/cache", output_file: str = "r1_api_summary.json"):
    """
    Create a lightweight summary JSON with just the key info (no full endpoint details)

    Args:
        cache_dir: Directory containing the analysis JSON files
        output_file: Name of the output summary JSON file
    """
    cache_path = Path(cache_dir)
    # Put output in specs folder, not cache folder
    output_path = cache_path.parent / output_file

    print(f"\n🔍 Creating lightweight summary...")
    print("=" * 80)

    # Find all analysis JSON files
    analysis_files = list(cache_path.glob("*_analysis.json"))

    if not analysis_files:
        print("❌ No analysis files found!")
        return None

    # Create summary structure
    summary = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "total_apis": 0,
            "total_endpoints": 0,
            "source": "Ruckus ONE OpenAPI Specifications"
        },
        "apis": []
    }

    total_endpoints = 0

    for analysis_file in sorted(analysis_files):
        try:
            with open(analysis_file, 'r') as f:
                analysis = json.load(f)

            # Extract just the summary info
            api_summary = {
                "name": analysis.get("name"),
                "version": analysis.get("version"),
                "url": analysis.get("url"),
                "base_url": analysis.get("base_url"),
                "endpoint_count": analysis.get("endpoint_count", 0),
                "rate_limit": analysis.get("rate_limit"),
                "downloaded_at": analysis.get("downloaded_at")
            }

            summary["apis"].append(api_summary)
            total_endpoints += api_summary["endpoint_count"]

        except Exception as e:
            print(f"  ✗ Error loading {analysis_file.name}: {e}")

    # Update metadata
    summary["metadata"]["total_apis"] = len(summary["apis"])
    summary["metadata"]["total_endpoints"] = total_endpoints

    # Sort by endpoint count (descending)
    summary["apis"].sort(key=lambda x: x["endpoint_count"], reverse=True)

    # Write summary file
    with open(output_path, 'w') as f:
        json.dump(summary, f, indent=2)

    file_size = output_path.stat().st_size
    file_size_kb = file_size / 1024

    print(f"✅ Created summary: {output_path}")
    print(f"   Total APIs: {len(summary['apis'])}")
    print(f"   Total endpoints: {total_endpoints}")
    print(f"   File size: {file_size_kb:.1f} KB")
    print("=" * 80)

    return str(output_path)


def create_openapi_spec(cache_dir: str = "./specs/cache", output_file: str = "full_r1_openapi_spec.json"):
    """
    Create a proper OpenAPI 3.0 compliant spec that merges all APIs
    This version loads the full YAML specs to get complete parameter/body/response details

    Args:
        cache_dir: Directory containing the analysis JSON files
        output_file: Name of the output OpenAPI spec file
    """
    import yaml

    cache_path = Path(cache_dir)
    # Put output in specs folder, not cache folder
    output_path = cache_path.parent / output_file

    print(f"\n🔍 Creating OpenAPI 3.0 compliant spec with full details...")
    print("=" * 80)

    # Find all YAML spec files (not analysis files - we want the full specs)
    yaml_files = list(cache_path.glob("*.yaml"))

    if not yaml_files:
        print("❌ No YAML spec files found!")
        return None

    print(f"✓ Found {len(yaml_files)} YAML spec files")

    # Create OpenAPI 3.0 structure
    openapi_spec = {
        "openapi": "3.0.1",
        "info": {
            "title": "Ruckus ONE Complete API",
            "version": "1.0.0",
            "description": "Consolidated OpenAPI specification for all Ruckus ONE APIs. "
                          "This combines 31 individual API specifications into a single document.",
            "contact": {
                "name": "Ruckus Cloud Support",
                "url": "https://support.ruckuswireless.com"
            }
        },
        "servers": [
            {
                "url": "https://api.ruckus.cloud",
                "description": "North America"
            },
            {
                "url": "https://api.eu.ruckus.cloud",
                "description": "Europe"
            },
            {
                "url": "https://api.asia.ruckus.cloud",
                "description": "Asia Pacific"
            }
        ],
        "paths": {},
        "tags": [],
        "components": {
            "schemas": {},
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "JWT token obtained from authentication endpoint"
                }
            }
        },
        "security": [
            {"BearerAuth": []}
        ]
    }

    # Track unique tags and schemas
    seen_tags = set()
    total_endpoints = 0
    path_conflicts = {}
    schema_counter = {}

    for yaml_file in sorted(yaml_files):
        try:
            # Load the full YAML spec
            with open(yaml_file, 'r') as f:
                spec_data = yaml.safe_load(f)

            # Get API info
            info = spec_data.get('info', {})
            api_name = info.get('title', yaml_file.stem)
            api_version = info.get('version', 'unknown')

            print(f"  Processing: {api_name} (v{api_version})")

            # Add tag for this API
            tag_name = api_name.replace(" API", "").replace(" REST API", "")
            if tag_name not in seen_tags:
                openapi_spec["tags"].append({
                    "name": tag_name,
                    "description": f"{api_name} (v{api_version})",
                })
                seen_tags.add(tag_name)

            # Merge schemas from this spec into components
            if 'components' in spec_data and 'schemas' in spec_data['components']:
                for schema_name, schema_def in spec_data['components']['schemas'].items():
                    # Prefix schema name with API name to avoid conflicts
                    prefixed_name = f"{tag_name}_{schema_name}"
                    openapi_spec["components"]["schemas"][prefixed_name] = schema_def

            # Process all paths/endpoints
            if 'paths' not in spec_data:
                continue

            for path, path_item in spec_data['paths'].items():
                # Track conflicts
                for method in ['get', 'post', 'put', 'patch', 'delete', 'options', 'head']:
                    if method not in path_item:
                        continue

                    conflict_key = f"{method}:{path}"
                    if conflict_key in path_conflicts:
                        path_conflicts[conflict_key].append(api_name)
                        continue  # Skip duplicate
                    else:
                        path_conflicts[conflict_key] = [api_name]

                    # Initialize path if doesn't exist
                    if path not in openapi_spec["paths"]:
                        openapi_spec["paths"][path] = {}

                    # Get the full operation object from the original spec
                    operation = path_item[method].copy()

                    # Add our tag (preserve existing tags too)
                    existing_tags = operation.get('tags', [])
                    if tag_name not in existing_tags:
                        existing_tags.insert(0, tag_name)
                    operation['tags'] = existing_tags

                    # Update schema references to use prefixed names
                    operation_str = json.dumps(operation)
                    operation_str = operation_str.replace('#/components/schemas/', f'#/components/schemas/{tag_name}_')
                    operation = json.loads(operation_str)

                    openapi_spec["paths"][path][method] = operation
                    total_endpoints += 1

        except Exception as e:
            print(f"  ✗ Error processing {yaml_file.name}: {e}")
            import traceback
            traceback.print_exc()

    # Report conflicts
    conflicts = {k: v for k, v in path_conflicts.items() if len(v) > 1}
    if conflicts:
        print(f"\n⚠️  Found {len(conflicts)} endpoint conflicts (same path/method in multiple APIs)")
        print("   Only the first occurrence was included in the spec")

    # Write OpenAPI spec
    with open(output_path, 'w') as f:
        json.dump(openapi_spec, f, indent=2)

    file_size = output_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)

    print("=" * 80)
    print(f"✅ Created OpenAPI 3.0 spec: {output_path}")
    print(f"   Total endpoints: {total_endpoints}")
    print(f"   Total paths: {len(openapi_spec['paths'])}")
    print(f"   Total tags: {len(openapi_spec['tags'])}")
    print(f"   Total schemas: {len(openapi_spec['components']['schemas'])}")
    print(f"   File size: {file_size_mb:.2f} MB")
    print(f"\n💡 This spec includes:")
    print(f"   ✓ Full parameters, headers, request bodies")
    print(f"   ✓ Complete response schemas")
    print(f"   ✓ All data models and type definitions")
    print(f"\n💡 Use with:")
    print(f"   - Swagger UI: https://editor.swagger.io")
    print(f"   - Postman: Import as OpenAPI 3.0 spec")
    print(f"   - Code generators: openapi-generator-cli")
    print("=" * 80)

    return str(output_path)


def main():
    """Main entry point"""
    print("\n" + "=" * 80)
    print("Ruckus ONE OpenAPI Consolidation Tool")
    print("=" * 80)

    # Create full consolidated file
    full_path = consolidate_analyses()

    # Create lightweight summary
    summary_path = create_summary_json()

    # Create OpenAPI 3.0 compliant spec
    openapi_path = create_openapi_spec()

    if full_path and summary_path and openapi_path:
        print("\n✅ All done! Generated files:")
        print(f"   1. {full_path}")
        print(f"      - Complete endpoint details for all APIs (custom format)")
        print(f"      - Use for detailed reference and data analysis")
        print(f"\n   2. {summary_path}")
        print(f"      - Lightweight API summary (no endpoint details)")
        print(f"      - Use for quick reference and dashboards")
        print(f"\n   3. {openapi_path}")
        print(f"      - OpenAPI 3.0 compliant specification")
        print(f"      - Use with Swagger UI, Postman, code generators")
        print("\n💡 Pro tip: Import these JSON files in your code:")
        print("""
    import json

    # Load full details (custom format)
    with open('specs/full_r1_openapi.json') as f:
        all_apis = json.load(f)

    # Access specific API
    guest_api = all_apis['apis']['Guest API']
    print(f"Guest API has {guest_api['endpoint_count']} endpoints")

    # Or use OpenAPI spec (standard format)
    with open('specs/full_r1_openapi_spec.json') as f:
        openapi = json.load(f)
    print(f"OpenAPI spec has {len(openapi['paths'])} paths")
        """)


if __name__ == "__main__":
    main()
