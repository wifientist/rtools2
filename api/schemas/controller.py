from typing import Optional, Literal, Union
from pydantic import BaseModel, Field


# ===== Base Schemas =====

class ControllerBase(BaseModel):
    """Base schema with common controller fields"""
    name: str = Field(..., description="User-friendly label for the controller")
    controller_type: Literal["RuckusONE", "SmartZone"]
    controller_subtype: Optional[Literal["MSP", "EC"]] = None


# ===== RuckusONE-Specific Schemas =====

class RuckusONEControllerCreate(BaseModel):
    """Schema for creating a RuckusONE controller"""
    name: str
    controller_type: Literal["RuckusONE"] = "RuckusONE"
    controller_subtype: Literal["MSP", "EC"] = Field(..., description="MSP or EC (End Customer)")
    r1_tenant_id: str = Field(..., description="R1's tenant identifier")
    r1_client_id: str = Field(..., description="OAuth2 client ID")
    r1_shared_secret: str = Field(..., description="OAuth2 shared secret")
    r1_region: Literal["NA", "EU", "APAC"] = Field(default="NA", description="Geographic region")


class RuckusONEControllerUpdate(BaseModel):
    """Schema for updating a RuckusONE controller"""
    name: Optional[str] = None
    controller_subtype: Optional[Literal["MSP", "EC"]] = None
    r1_tenant_id: Optional[str] = None
    r1_client_id: Optional[str] = None
    r1_shared_secret: Optional[str] = None
    r1_region: Optional[Literal["NA", "EU", "APAC"]] = None


# ===== SmartZone-Specific Schemas =====

class SmartZoneControllerCreate(BaseModel):
    """Schema for creating a SmartZone controller (stubbed for future implementation)"""
    name: str
    controller_type: Literal["SmartZone"] = "SmartZone"
    sz_host: str = Field(..., description="Controller hostname or IP address")
    sz_port: int = Field(default=8443, description="API port")
    sz_use_https: bool = Field(default=True, description="Use HTTPS")
    sz_username: str = Field(..., description="API username")
    sz_password: str = Field(..., description="API password")
    sz_version: Optional[str] = Field(None, description="Controller version (e.g., '6.1', '7.0')")


class SmartZoneControllerUpdate(BaseModel):
    """Schema for updating a SmartZone controller"""
    name: Optional[str] = None
    sz_host: Optional[str] = None
    sz_port: Optional[int] = None
    sz_use_https: Optional[bool] = None
    sz_username: Optional[str] = None
    sz_password: Optional[str] = None
    sz_version: Optional[str] = None


# ===== Union Type for API Endpoints =====

ControllerCreate = Union[RuckusONEControllerCreate, SmartZoneControllerCreate]
ControllerUpdate = Union[RuckusONEControllerUpdate, SmartZoneControllerUpdate]


# ===== Response Schemas =====

class ControllerResponse(BaseModel):
    """Schema for controller responses (hides sensitive fields)"""
    id: int
    name: str
    controller_type: str
    controller_subtype: Optional[str] = None

    # RuckusONE fields (only present for RuckusONE controllers)
    r1_tenant_id: Optional[str] = None
    r1_region: Optional[str] = None

    # SmartZone fields (only present for SmartZone controllers)
    sz_host: Optional[str] = None
    sz_port: Optional[int] = None
    sz_version: Optional[str] = None

    class Config:
        from_attributes = True


class ControllerDetailResponse(ControllerResponse):
    """Extended response with timestamps"""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ===== Request Schemas for Controller Selection =====

class SetActiveControllerRequest(BaseModel):
    """Request to set active controller"""
    controller_id: int


class SetSecondaryControllerRequest(BaseModel):
    """Request to set secondary controller"""
    controller_id: int


# ===== Controller Info for Dynamic Routing =====

class UserControllerInfo(BaseModel):
    """Controller information for user-available controllers"""
    controller_id: int
    name: str
    controller_type: str
    controller_subtype: Optional[str] = None
    is_active: bool
    is_secondary: bool
