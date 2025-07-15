# api/clients/r1_client.py

from fastapi import HTTPException, Depends, Request
from sqlalchemy.orm import Session
from dependencies import get_db
from dependencies import get_current_user
from models.user import User
from models.tenant import Tenant
from r1api.client import R1Client
from utils.encryption import decrypt_value

def create_r1_client_from_tenant(tenant_pk: int, db: Session) -> R1Client:
    """Create a fresh R1Client using credentials from a Tenant record."""

    print(f"create_r1_client_from_tenant - Fetching tenant with primary key: {tenant_pk}")

    # Note: Using tenant_pk (primary key) to avoid confusion with tenant_id that R1 uses.
    tenant = db.query(Tenant).filter(Tenant.id == tenant_pk).first()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant with ID {tenant_pk} not found.")

    try:
        decrypted_client_id = decrypt_value(tenant.encrypted_client_id)
        decrypted_shared_secret = decrypt_value(tenant.encrypted_shared_secret)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error decrypting tenant credentials: {str(e)}")

    region = tenant.region if hasattr(tenant, "region") and tenant.region else "US"  # Optional: if you add region later

    ec_type = tenant.ec_type if hasattr(tenant, "ec_type") else None 

    return R1Client(
        tenant_id=tenant.tenant_id,
        client_id=decrypted_client_id,
        shared_secret=decrypted_shared_secret,
        region=region,
        ec_type=ec_type,
    )

# def get_r1_client(
#     current_user: User = Depends(get_current_user),
#     db: Session = Depends(get_db),
#     tenant_id: str = None
# ):
#     """Get an R1Client using the user's chosen tenant."""
#     if tenant_id is None:
#         tenant_id = current_user.active_tenant_id
#         if tenant_id is None:
#             raise HTTPException(status_code=400, detail="No active tenant selected.")
    
#     # Fetch the tenant using the provided tenant_id
#     return create_r1_client_from_tenant(tenant_id, db)

def get_r1_client(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    print("get_r1_client - Fetching R1Client for user:", current_user.email)
    tenant_id = current_user.active_tenant_id
    if tenant_id is None:
        raise HTTPException(status_code=400, detail="No active tenant selected.")

    return create_r1_client_from_tenant(tenant_id, db)

# def get_secondary_r1_client(
#     current_user: User = Depends(get_current_user),
#     db: Session = Depends(get_db),
# ):
#     """Get a secondary R1Client using the tenant's primary key."""
#     tenant_id = current_user.secondary_tenant_id   
#     if tenant_id is None:
#         raise HTTPException(status_code=400, detail="No secondary tenant selected.")

#     return create_r1_client_from_tenant(tenant_id, db)

def get_r1_clients(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    print(" %%%%%%%%%%%% get_r1_CLIENTS - Fetching R1Clients for user:", current_user.email)
    print(f"Active Tenant ID: {current_user.active_tenant_id}")
    print(f"Secondary Tenant ID: {current_user.secondary_tenant_id}")
    active = create_r1_client_from_tenant(current_user.active_tenant_id, db)
    secondary = create_r1_client_from_tenant(current_user.secondary_tenant_id, db)
    print(f"Active Tenant R1Client: {active}")
    print(f"Secondary Tenant R1Client: {secondary}")
    return {"active": active, "secondary": secondary}

# Scoped R1Client Dependency
def get_scoped_r1_client(selector: str):
    async def _get_client(r1_clients=Depends(get_r1_clients)):
        """Get a specific R1Client based on the selector."""
        print(f" $$$$$$$$$ get_scoped_r1_client - Fetching R1Client for selector: {selector}")
        client = r1_clients[selector]
        if not client:
            raise HTTPException(status_code=404, detail=f"R1Client for {selector} not found.")
        if getattr(client, "auth_failed", False):
            raise HTTPException(status_code=401, detail="R1Client authentication failed")
        return client
    return _get_client


def dynamic_r1_client(request: Request, r1_clients=Depends(get_r1_clients)) -> R1Client:

    print(f" @@@@@@@@@@@@@@@@ dynamic_r1_client - Request path: {request.url.path}")
    # Infer from path prefix
    if request.url.path.startswith("/r1b"):
        selector = "secondary"
    else:
        selector = "active"

    client = r1_clients[selector]
    if getattr(client, "auth_failed", False):
        raise HTTPException(status_code=401, detail="R1Client authentication failed")

    print(f" ++++++ Dynamic R1Client selected: {selector}")

    return client
