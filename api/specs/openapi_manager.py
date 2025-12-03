"""
OpenAPI Spec Manager

Service to download, cache, and analyze Ruckus ONE OpenAPI specifications.
Helps track what endpoints are available vs what we've implemented.
"""

import os
import json
import yaml
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Set
from datetime import datetime
import aiohttp
from dataclasses import dataclass, asdict

# Handle both direct execution and module import
try:
    from .r1_openapi_specs import R1_OPENAPI_SPECS, OpenAPISpec, SpecStatus
except ImportError:
    from r1_openapi_specs import R1_OPENAPI_SPECS, OpenAPISpec, SpecStatus


@dataclass
class EndpointInfo:
    """Information about a single API endpoint"""
    path: str
    method: str
    summary: Optional[str] = None
    description: Optional[str] = None
    operation_id: Optional[str] = None
    tags: List[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class SpecAnalysis:
    """Analysis results for an OpenAPI spec"""
    name: str
    version: str
    url: str
    downloaded_at: str
    endpoint_count: int
    endpoints: List[EndpointInfo]
    base_url: Optional[str] = None
    rate_limit: Optional[str] = None


class OpenAPIManager:
    """Manages downloading and analyzing OpenAPI specifications"""

    def __init__(self, cache_dir: str = "./specs/cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.specs_data: Dict[str, SpecAnalysis] = {}

    def _get_cache_path(self, spec: OpenAPISpec) -> Path:
        """Get the cache file path for a spec"""
        # Create a safe filename from the spec name and version
        safe_name = spec.name.replace(" ", "_").replace("/", "_").lower()
        filename = f"{safe_name}-{spec.version}.yaml"
        return self.cache_dir / filename

    def _get_analysis_path(self, spec: OpenAPISpec) -> Path:
        """Get the analysis JSON file path for a spec"""
        safe_name = spec.name.replace(" ", "_").replace("/", "_").lower()
        filename = f"{safe_name}-{spec.version}_analysis.json"
        return self.cache_dir / filename

    async def download_spec(self, spec: OpenAPISpec, force: bool = False) -> Optional[str]:
        """
        Download a single OpenAPI spec

        Args:
            spec: The spec to download
            force: If True, download even if cached

        Returns:
            Path to the downloaded file, or None if failed
        """
        cache_path = self._get_cache_path(spec)

        # Check if already cached
        if cache_path.exists() and not force:
            print(f"✓ Using cached spec: {spec.name} v{spec.version}")
            return str(cache_path)

        print(f"⬇️  Downloading: {spec.name} v{spec.version}")
        print(f"   URL: {spec.url}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(spec.url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        content = await response.text()

                        # Save to cache
                        with open(cache_path, 'w') as f:
                            f.write(content)

                        print(f"✓ Downloaded: {spec.name} v{spec.version}")
                        return str(cache_path)
                    else:
                        print(f"✗ Failed to download {spec.name}: HTTP {response.status}")
                        return None
        except Exception as e:
            print(f"✗ Error downloading {spec.name}: {str(e)}")
            return None

    async def download_all_specs(self, force: bool = False, status_filter: Optional[SpecStatus] = None) -> Dict[str, str]:
        """
        Download all OpenAPI specs

        Args:
            force: If True, re-download even if cached
            status_filter: If provided, only download specs with this status

        Returns:
            Dict mapping spec name to cache path
        """
        specs_to_download = R1_OPENAPI_SPECS

        if status_filter:
            specs_to_download = [s for s in specs_to_download if s.status == status_filter]

        print(f"\n📥 Downloading {len(specs_to_download)} OpenAPI specifications...")
        print("=" * 80)

        # Download all specs concurrently
        tasks = [self.download_spec(spec, force) for spec in specs_to_download]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Build result map
        downloaded = {}
        failed = []

        for spec, result in zip(specs_to_download, results):
            if isinstance(result, Exception):
                print(f"✗ Exception for {spec.name}: {result}")
                failed.append(spec.name)
            elif result:
                downloaded[spec.name] = result
            else:
                failed.append(spec.name)

        print("\n" + "=" * 80)
        print(f"✓ Downloaded: {len(downloaded)}")
        print(f"✗ Failed: {len(failed)}")

        if failed:
            print(f"\nFailed specs: {', '.join(failed)}")

        return downloaded

    def analyze_spec(self, spec: OpenAPISpec) -> Optional[SpecAnalysis]:
        """
        Analyze a downloaded OpenAPI spec to extract endpoints and metadata

        Args:
            spec: The spec to analyze

        Returns:
            SpecAnalysis object or None if failed
        """
        cache_path = self._get_cache_path(spec)

        if not cache_path.exists():
            print(f"✗ Spec not found in cache: {spec.name}")
            return None

        try:
            with open(cache_path, 'r') as f:
                spec_data = yaml.safe_load(f)

            # Extract endpoints
            endpoints = []
            if 'paths' in spec_data:
                for path, methods in spec_data['paths'].items():
                    for method, details in methods.items():
                        if method.lower() in ['get', 'post', 'put', 'patch', 'delete']:
                            endpoint = EndpointInfo(
                                path=path,
                                method=method.upper(),
                                summary=details.get('summary'),
                                description=details.get('description'),
                                operation_id=details.get('operationId'),
                                tags=details.get('tags', [])
                            )
                            endpoints.append(endpoint)

            # Extract base URL
            base_url = None
            if 'servers' in spec_data and spec_data['servers']:
                base_url = spec_data['servers'][0].get('url')

            # Look for rate limit info in description
            rate_limit = None
            info = spec_data.get('info', {})
            description = info.get('description', '')
            if 'rate' in description.lower() or 'limit' in description.lower():
                # Extract rate limit info from description
                lines = description.split('\n')
                for line in lines:
                    if 'rate' in line.lower() or 'limit' in line.lower():
                        rate_limit = line.strip()
                        break

            analysis = SpecAnalysis(
                name=spec.name,
                version=spec.version,
                url=spec.url,
                downloaded_at=datetime.now().isoformat(),
                endpoint_count=len(endpoints),
                endpoints=endpoints,
                base_url=base_url,
                rate_limit=rate_limit
            )

            # Cache the analysis
            analysis_path = self._get_analysis_path(spec)
            with open(analysis_path, 'w') as f:
                json.dump(self._analysis_to_dict(analysis), f, indent=2)

            self.specs_data[spec.name] = analysis
            return analysis

        except Exception as e:
            print(f"✗ Error analyzing {spec.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

    def _analysis_to_dict(self, analysis: SpecAnalysis) -> dict:
        """Convert SpecAnalysis to dict for JSON serialization"""
        return {
            "name": analysis.name,
            "version": analysis.version,
            "url": analysis.url,
            "downloaded_at": analysis.downloaded_at,
            "endpoint_count": analysis.endpoint_count,
            "base_url": analysis.base_url,
            "rate_limit": analysis.rate_limit,
            "endpoints": [
                {
                    "path": e.path,
                    "method": e.method,
                    "summary": e.summary,
                    "description": e.description,
                    "operation_id": e.operation_id,
                    "tags": e.tags
                }
                for e in analysis.endpoints
            ]
        }

    def analyze_all_specs(self) -> Dict[str, SpecAnalysis]:
        """
        Analyze all downloaded specs

        Returns:
            Dict mapping spec name to analysis
        """
        print("\n🔍 Analyzing OpenAPI specifications...")
        print("=" * 80)

        analyses = {}
        for spec in R1_OPENAPI_SPECS:
            analysis = self.analyze_spec(spec)
            if analysis:
                analyses[spec.name] = analysis
                print(f"✓ Analyzed: {spec.name} - {analysis.endpoint_count} endpoints")

        print("\n" + "=" * 80)
        print(f"✓ Analyzed {len(analyses)} specifications")

        return analyses

    def get_all_endpoints(self) -> Dict[str, List[EndpointInfo]]:
        """
        Get all endpoints grouped by API name

        Returns:
            Dict mapping API name to list of endpoints
        """
        endpoints = {}
        for name, analysis in self.specs_data.items():
            endpoints[name] = analysis.endpoints
        return endpoints

    def get_endpoint_summary(self) -> Dict[str, int]:
        """
        Get summary of endpoint counts per API

        Returns:
            Dict mapping API name to endpoint count
        """
        return {
            name: analysis.endpoint_count
            for name, analysis in self.specs_data.items()
        }

    def export_endpoint_list(self, output_path: str = None):
        """
        Export a comprehensive list of all endpoints to a markdown file

        Args:
            output_path: Path to write the markdown file (defaults to specs/endpoint_list.md)
        """
        if output_path is None:
            # Put in specs folder, not cache
            output_path = str(Path(__file__).parent / "endpoint_list.md")

        with open(output_path, 'w') as f:
            f.write("# Ruckus ONE API Endpoints\n\n")
            f.write(f"Generated: {datetime.now().isoformat()}\n\n")
            f.write(f"Total APIs: {len(self.specs_data)}\n")

            total_endpoints = sum(a.endpoint_count for a in self.specs_data.values())
            f.write(f"Total Endpoints: {total_endpoints}\n\n")
            f.write("---\n\n")

            for spec_name in sorted(self.specs_data.keys()):
                analysis = self.specs_data[spec_name]
                f.write(f"## {analysis.name} (v{analysis.version})\n\n")

                if analysis.base_url:
                    f.write(f"**Base URL:** `{analysis.base_url}`\n\n")

                if analysis.rate_limit:
                    f.write(f"**Rate Limit:** {analysis.rate_limit}\n\n")

                f.write(f"**Endpoint Count:** {analysis.endpoint_count}\n\n")

                # Group endpoints by tag
                tagged_endpoints: Dict[str, List[EndpointInfo]] = {}
                untagged = []

                for endpoint in analysis.endpoints:
                    if endpoint.tags:
                        for tag in endpoint.tags:
                            if tag not in tagged_endpoints:
                                tagged_endpoints[tag] = []
                            tagged_endpoints[tag].append(endpoint)
                    else:
                        untagged.append(endpoint)

                # Write endpoints by tag
                for tag in sorted(tagged_endpoints.keys()):
                    f.write(f"### {tag}\n\n")
                    f.write("| Method | Path | Summary |\n")
                    f.write("|--------|------|----------|\n")

                    for endpoint in sorted(tagged_endpoints[tag], key=lambda e: (e.path, e.method)):
                        summary = endpoint.summary or ""
                        f.write(f"| `{endpoint.method}` | `{endpoint.path}` | {summary} |\n")

                    f.write("\n")

                # Write untagged endpoints
                if untagged:
                    f.write("### Other Endpoints\n\n")
                    f.write("| Method | Path | Summary |\n")
                    f.write("|--------|------|----------|\n")

                    for endpoint in sorted(untagged, key=lambda e: (e.path, e.method)):
                        summary = endpoint.summary or ""
                        f.write(f"| `{endpoint.method}` | `{endpoint.path}` | {summary} |\n")

                    f.write("\n")

                f.write("---\n\n")

        print(f"✓ Exported endpoint list to: {output_path}")

    def compare_with_implementation(self, implemented_services: List[str]) -> Dict[str, any]:
        """
        Compare OpenAPI specs with what we've implemented

        Args:
            implemented_services: List of service names we've implemented

        Returns:
            Dict with comparison results
        """
        all_api_names = set(analysis.name for analysis in self.specs_data.values())
        implemented = set(implemented_services)

        missing = all_api_names - implemented
        extra = implemented - all_api_names

        return {
            "total_apis": len(all_api_names),
            "implemented": len(implemented),
            "missing": sorted(list(missing)),
            "extra": sorted(list(extra)),
            "coverage_percent": (len(implemented & all_api_names) / len(all_api_names) * 100) if all_api_names else 0
        }


async def main():
    """Main function to download and analyze all specs"""
    manager = OpenAPIManager()

    # Download all specs
    await manager.download_all_specs(force=False)

    # Analyze all specs
    manager.analyze_all_specs()

    # Export endpoint list
    output_path = manager.cache_dir / "endpoint_list.md"
    manager.export_endpoint_list(str(output_path))

    # Print summary
    print("\n📊 Summary:")
    print("=" * 80)
    summary = manager.get_endpoint_summary()
    for api_name, count in sorted(summary.items()):
        print(f"{api_name}: {count} endpoints")


if __name__ == "__main__":
    asyncio.run(main())
