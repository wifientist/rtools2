"""
Ruckus ONE OpenAPI Specification Registry

This module maintains the registry of all Ruckus ONE OpenAPI specification URLs
and provides functionality to download, analyze, and compare them with our
implemented services.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
from enum import Enum


class SpecStatus(str, Enum):
    """Status of the API spec"""
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    UNKNOWN = "unknown"  # For specs with ?? marker


@dataclass
class OpenAPISpec:
    """Represents a single OpenAPI specification"""
    name: str
    version: str
    url: str
    status: SpecStatus = SpecStatus.ACTIVE
    notes: Optional[str] = None


# Registry of all known Ruckus ONE OpenAPI specifications
R1_OPENAPI_SPECS: List[OpenAPISpec] = [
    # Deprecated APIs
    OpenAPISpec(
        name="Entitlement Assignment Endpoints",
        version="0.2.0",
        url="https://docs.ruckus.cloud/_bundle/deprecated_apis/pentitlement-assign-0.2.0.yaml",
        status=SpecStatus.DEPRECATED
    ),
    OpenAPISpec(
        name="Property Management REST API",
        version="1.0.0",
        url="https://docs.ruckus.cloud/_bundle/deprecated_apis/property-management-1.0.1.yaml",
        status=SpecStatus.DEPRECATED
    ),
    OpenAPISpec(
        name="Manage Entitlements API",
        version="0.2.0",
        url="https://docs.ruckus.cloud/_bundle/deprecated_apis/entitlement-0.2.0.yaml",
        status=SpecStatus.DEPRECATED
    ),

    # Active APIs
    OpenAPISpec(
        name="Message Template API",
        version="0.0.12",
        url="https://docs.ruckus.cloud/_bundle/api/msg_template_management-0.0.12.yaml",
        status=SpecStatus.ACTIVE
    ),
    OpenAPISpec(
        name="Property Management REST API",
        version="1.0.1",
        url="https://docs.ruckus.cloud/_bundle/api/property-management-1.0.1.yaml",
        status=SpecStatus.ACTIVE
    ),
    OpenAPISpec(
        name="DPSK Service",
        version="0.0.3",
        url="https://docs.ruckus.cloud/_bundle/api/dpsk-api-0.0.3.yaml",
        status=SpecStatus.ACTIVE
    ),
    OpenAPISpec(
        name="Activities API",
        version="0.0.1",
        url="https://docs.ruckus.cloud/_bundle/api/activity-0.0.1.yaml",
        status=SpecStatus.ACTIVE
    ),
    OpenAPISpec(
        name="Manage Entitlements API",
        version="0.2.0",
        url="https://docs.ruckus.cloud/_bundle/api/entitlement-0.2.0.yaml",
        status=SpecStatus.ACTIVE
    ),
    OpenAPISpec(
        name="Device Enrollment REST API",
        version="0.0.1",
        url="https://docs.ruckus.cloud/_bundle/api/device-enrollment-api-0.0.1.yaml",
        status=SpecStatus.ACTIVE
    ),
    OpenAPISpec(
        name="MAC Registration API",
        version="0.0.1",
        url="https://docs.ruckus.cloud/_bundle/api/mac-registration-0.0.1.yaml",
        status=SpecStatus.ACTIVE
    ),
    OpenAPISpec(
        name="Events and Alarms API",
        version="0.0.3",
        url="https://docs.ruckus.cloud/_bundle/api/event-alarm-api-0.0.3.yaml",
        status=SpecStatus.ACTIVE
    ),
    OpenAPISpec(
        name="Admin Enrollment REST API",
        version="0.0.1",
        url="https://docs.ruckus.cloud/_bundle/api/enrollment-api-0.0.1.yaml",
        status=SpecStatus.ACTIVE
    ),
    OpenAPISpec(
        name="Config Template Service API",
        version="1.0.0",
        url="https://docs.ruckus.cloud/_bundle/api/cfg-template-service-1.0.0.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Certificate Template API",
        version="0.0.1",
        url="https://docs.ruckus.cloud/_bundle/api/certificate-template-api-0.0.1.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="RUCKUS Edge API",
        version="1.0.3",
        url="https://docs.ruckus.cloud/_bundle/api/edge-api-1.0.3.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="External Auth API",
        version="0.0.1",
        url="https://docs.ruckus.cloud/_bundle/api/external-auth-0.0.1.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="File service API",
        version="0.2.7",
        url="https://docs.ruckus.cloud/_bundle/api/file-0.2.7.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Guest API",
        version="1.7.1",
        url="https://docs.ruckus.cloud/_bundle/api/guest-1.7.1.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="MSP Services",
        version="0.3.3",
        url="https://docs.ruckus.cloud/_bundle/api/mspservice-0.3.3.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Identity Management",
        version="0.0.2",
        url="https://docs.ruckus.cloud/_bundle/api/persona-0.0.2.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Policy Management API",
        version="0.0.3",
        url="https://docs.ruckus.cloud/_bundle/api/policy-evaluator-0.0.3.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Adaptive Policy Management",
        version="0.0.9",
        url="https://docs.ruckus.cloud/_bundle/api/policy-mgmt-0.0.9.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Property Management",
        version="1.0.1",
        url="https://docs.ruckus.cloud/_bundle/api/property-management-1.0.1.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list (duplicate version)"
    ),
    OpenAPISpec(
        name="Tenant Management",
        version="0.3.0",
        url="https://docs.ruckus.cloud/_bundle/api/ptenant-0.3.0.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="RADIUS Attribute Group Management API",
        version="1.0.8",
        url="https://docs.ruckus.cloud/_bundle/api/radiusattribgroup-1.0.8.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Resident Portal API",
        version="0.0.1",
        url="https://docs.ruckus.cloud/_bundle/api/resident-portal-0.0.1.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Switch Service API",
        version="0.4.0",
        url="https://docs.ruckus.cloud/_bundle/api/switch-0.4.0.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Venue Service API",
        version="0.2.8",
        url="https://docs.ruckus.cloud/_bundle/api/venue-0.2.8.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="ViewModel service API",
        version="1.0.42",
        url="https://docs.ruckus.cloud/_bundle/api/viewmodel-1.0.42.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="WiFi API",
        version="17.3.3.205",
        url="https://docs.ruckus.cloud/_bundle/api/wifi-17.3.3.205.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Workflow Actions API",
        version="0.0.2",
        url="https://docs.ruckus.cloud/_bundle/api/workflow-actions-0.0.2.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
    OpenAPISpec(
        name="Workflow Management API",
        version="0.0.3",
        url="https://docs.ruckus.cloud/_bundle/api/workflow-api-0.0.3.yaml",
        status=SpecStatus.UNKNOWN,
        notes="Marked with ?? in original list"
    ),
]


def get_specs_by_status(status: SpecStatus) -> List[OpenAPISpec]:
    """Filter specs by their status"""
    return [spec for spec in R1_OPENAPI_SPECS if spec.status == status]


def get_spec_by_name(name: str) -> Optional[OpenAPISpec]:
    """Get a specific spec by name"""
    for spec in R1_OPENAPI_SPECS:
        if spec.name.lower() == name.lower():
            return spec
    return None


def get_all_spec_urls() -> List[str]:
    """Get all spec URLs"""
    return [spec.url for spec in R1_OPENAPI_SPECS]


def get_spec_summary() -> Dict[str, int]:
    """Get summary statistics about the specs"""
    return {
        "total": len(R1_OPENAPI_SPECS),
        "active": len(get_specs_by_status(SpecStatus.ACTIVE)),
        "deprecated": len(get_specs_by_status(SpecStatus.DEPRECATED)),
        "unknown": len(get_specs_by_status(SpecStatus.UNKNOWN)),
    }
