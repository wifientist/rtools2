# api/clients/r1_client.py

from fastapi import HTTPException, Depends, Request, Path
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

def get_r1_active_client(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get an R1Client using the user's active tenant."""
    print("get_r1_client - Fetching R1Client for user:", current_user.email)
    tenant_id = current_user.active_tenant_id
    if tenant_id is None:
        raise HTTPException(status_code=400, detail="No active tenant selected.")

    return create_r1_client_from_tenant(tenant_id, db)

def get_r1_clients(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get both active and secondary R1Clients for backward compatibility."""
    print(" %%%%%%%%%%%% get_r1_CLIENTS - Fetching R1Clients for user:", current_user.email)
    print(f"Active Tenant ID: {current_user.active_tenant_id}")
    print(f"Secondary Tenant ID: {current_user.secondary_tenant_id}")
    active = create_r1_client_from_tenant(current_user.active_tenant_id, db) if current_user.active_tenant_id else None
    secondary = create_r1_client_from_tenant(current_user.secondary_tenant_id, db) if current_user.secondary_tenant_id else None
    print(f"Active Tenant R1Client: {active}")
    print(f"Secondary Tenant R1Client: {secondary}")
    return {"active": active, "secondary": secondary}

# def validate_tenant_access(tenant_id: str, user: User, db: Session) -> Tenant:
#     """
#     Validate that the tenant exists and the user has access to it.
#     Returns the Tenant object if valid, raises HTTPException otherwise.
#     """
#     # First, find the tenant by tenant_id (not primary key)
#     tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
#     if not tenant:
#         raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found.")
    
#     # Check if user has access to this tenant
#     # Assuming tenants are associated with users through company_id or direct ownership
#     # You may need to adjust this logic based on your specific authorization model
    
#     # Option 1: Check if tenant belongs to user's company
#     #if hasattr(tenant, 'company_id') and tenant.company_id != user.company_id:
#     #    raise HTTPException(status_code=403, detail=f"Access denied to tenant '{tenant_id}'.")
    
#     # Option 2: Check if tenant is one of user's active/secondary tenants
#     user_tenant_ids = []
#     if user.active_tenant_id:
#         active_tenant = db.query(Tenant).filter(Tenant.id == user.active_tenant_id).first()
#         if active_tenant:
#             user_tenant_ids.append(active_tenant.tenant_id)
    
#     if user.secondary_tenant_id:
#         secondary_tenant = db.query(Tenant).filter(Tenant.id == user.secondary_tenant_id).first()
#         if secondary_tenant:
#             user_tenant_ids.append(secondary_tenant.tenant_id)
    
#     if tenant_id not in user_tenant_ids:
#         raise HTTPException(status_code=403, detail=f"Access denied to tenant '{tenant_id}'. User does not have permission to access this tenant.")
    
#     return tenant

def validate_tenant_access(tenant_id: str, user: User, db: Session) -> Tenant:
    """
    Validate that the tenant exists and the user has access to it.
    Returns the Tenant object if valid, raises HTTPException otherwise.
    """
    # Single query: find tenant that belongs to this user
    tenant = db.query(Tenant).filter(
        Tenant.id == tenant_id,  # Note: using tenant_id (the R1 identifier)
        Tenant.user_id == user.id
    ).first()
    
    if not tenant:
        # Check if tenant exists at all to give appropriate error message
        tenant_exists = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant_exists:
            raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found.")
        else:
            raise HTTPException(status_code=403, detail=f"Access denied to tenant '{tenant_id}'.")
    
    return tenant

def get_dynamic_r1_client(
    tenant_pk: str = Path(..., description="Tenant PK"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> R1Client:
    """
    Get an R1Client for a specific tenant ID with proper authorization checks.
    This is the main dependency for the new dynamic routing system.
    """
    print(f"get_dynamic_r1_client - Fetching R1Client for tenant: {tenant_pk}, user: {current_user.email}")
    
    # Validate tenant access
    tenant = validate_tenant_access(tenant_pk, current_user, db)
    
    # Create and return the R1Client
    try:
        client = create_r1_client_from_tenant(tenant.id, db)  # Using primary key
        
        # Check if authentication failed
        if getattr(client, "auth_failed", False):
            raise HTTPException(status_code=401, detail="R1Client authentication failed")
        
        print(f"Successfully created R1Client for tenant: {tenant_pk}")
        return client
        
    except Exception as e:
        print(f"Error creating R1Client for tenant {tenant_pk}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create R1Client for tenant '{tenant_pk}': {str(e)}")

# Legacy support functions (kept for backward compatibility)
# def get_scoped_r1_client(selector: str):
#     """Legacy function for backward compatibility with existing /r1a and /r1b routes."""
#     async def _get_client(r1_clients=Depends(get_r1_clients)):
#         print(f" $$$$$$$$$ get_scoped_r1_client - Fetching R1Client for selector: {selector}")
#         client = r1_clients[selector]
#         if not client:
#             raise HTTPException(status_code=404, detail=f"R1Client for {selector} not found.")
#         if getattr(client, "auth_failed", False):
#             raise HTTPException(status_code=401, detail="R1Client authentication failed")
#         return client
#     return _get_client

# def get_r1_client_by_path(request: Request, r1_clients=Depends(get_r1_clients)) -> R1Client:
#     """Choose appropriate client based on path-based routing."""
#     print(f" @@@@@@@@@@@@@@@@ get_r1_client_by_path - Request path: {request.url.path}")
#     # Infer from path prefix
#     if request.url.path.startswith("/r1/0"):  #TODO change this to regex the tenant_pk from the path
#         selector = "secondary"
#     else:
#         selector = "active"

#     client = r1_clients[selector]
#     if getattr(client, "auth_failed", False):
#         raise HTTPException(status_code=401, detail="R1Client authentication failed")

#     print(f" ++++++ Dynamic R1Client selected: {selector}")

#     return client

def get_tenant_aware_r1_client(
    request: Request, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)) -> R1Client:

    """
    Extract tenant_id from the request path and return appropriate R1Client.
    This allows existing endpoints to work without modification.
    """
    path_parts = request.url.path.strip('/').split('/')
    
    # Find tenant_id in path (should be after 'r1')
    try:
        r1_index = path_parts.index('r1')
        if r1_index + 1 < len(path_parts):
            tenant_id = path_parts[r1_index + 1]
            return get_dynamic_r1_client(tenant_id, current_user, db)
    except (ValueError, IndexError):
        pass
    
    raise HTTPException(status_code=400, detail="Could not extract tenant_id from request path")
