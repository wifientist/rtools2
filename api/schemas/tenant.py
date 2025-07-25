from typing import Optional
from pydantic import BaseModel

class TenantCreate(BaseModel):
    name: str
    tenant_id: str
    client_id: str
    shared_secret: str
    ec_type: Optional[str] = None

class SetActiveTenantRequest(BaseModel):
    tenant_id: int

class SetActiveTenantResponse(BaseModel):
    id: int
    name: str
    tenant_id: str

class SetSecondaryTenantRequest(BaseModel):
    tenant_id: int

class SetSecondaryTenantResponse(BaseModel):
    id: int
    name: str
    tenant_id: str
