# api/clients/r1_client.py

from fastapi import HTTPException, Depends
from sqlalchemy.orm import Session
from dependencies import get_db
from dependencies import get_current_user
from models.user import User
from models.tenant import Tenant
from r1api.client import R1Client
from utils.encryption import decrypt_value

def create_r1_client_from_tenant(tenant_pk: int, db: Session) -> R1Client:
    """Create a fresh R1Client using credentials from a Tenant record."""

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

    return R1Client(
        tenant_id=tenant.tenant_id,
        client_id=decrypted_client_id,
        shared_secret=decrypted_shared_secret,
        region=region
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