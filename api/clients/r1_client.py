# api/clients/r1_client.py

from fastapi import HTTPException, Depends, Request, Path
from sqlalchemy.orm import Session
from dependencies import get_db
from dependencies import get_current_user
from models.user import User
from models.controller import Controller
from r1api.client import R1Client
from utils.encryption import decrypt_value


def create_r1_client_from_controller(controller_id: int, db: Session) -> R1Client:
    """
    Create a fresh R1Client using credentials from a Controller record.
    Only works for RuckusONE controllers.

    Args:
        controller_id: Database primary key of the controller
        db: Database session

    Returns:
        R1Client configured with controller credentials

    Raises:
        HTTPException: If controller not found or not RuckusONE type
    """
    print(f"create_r1_client_from_controller - Fetching controller with ID: {controller_id}")

    controller = db.query(Controller).filter(Controller.id == controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail=f"Controller with ID {controller_id} not found.")

    # Verify this is a RuckusONE controller
    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create R1 client for {controller.controller_type} controller '{controller.name}'. Only RuckusONE controllers are supported for this operation."
        )

    try:
        decrypted_client_id = controller.get_r1_client_id()
        decrypted_shared_secret = controller.get_r1_shared_secret()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error decrypting controller credentials: {str(e)}")

    region = controller.r1_region if controller.r1_region else "NA"

    # R1Client still uses "tenant_id" because that's R1's API terminology
    # We're passing R1's tenant identifier, not our controller ID
    return R1Client(
        tenant_id=controller.r1_tenant_id,  # R1's tenant identifier
        client_id=decrypted_client_id,
        shared_secret=decrypted_shared_secret,
        region=region,
        ec_type=controller.controller_subtype,  # "MSP" or "EC"
    )

def get_r1_active_client(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get an R1Client using the user's active controller."""
    print("get_r1_active_client - Fetching R1Client for user:", current_user.email)
    controller_id = current_user.active_controller_id
    if controller_id is None:
        raise HTTPException(status_code=400, detail="No active controller selected.")

    return create_r1_client_from_controller(controller_id, db)


def get_r1_clients(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get both active and secondary R1Clients for backward compatibility."""
    print("get_r1_clients - Fetching R1Clients for user:", current_user.email)
    print(f"Active Controller ID: {current_user.active_controller_id}")
    print(f"Secondary Controller ID: {current_user.secondary_controller_id}")
    active = create_r1_client_from_controller(current_user.active_controller_id, db) if current_user.active_controller_id else None
    secondary = create_r1_client_from_controller(current_user.secondary_controller_id, db) if current_user.secondary_controller_id else None
    print(f"Active Controller R1Client: {active}")
    print(f"Secondary Controller R1Client: {secondary}")
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

def validate_controller_access(controller_id: int, user: User, db: Session) -> Controller:
    """
    Validate that the controller exists and the user has access to it.
    Returns the Controller object if valid, raises HTTPException otherwise.

    Args:
        controller_id: Database primary key of the controller
        user: Current user
        db: Database session

    Returns:
        Controller object if access is valid

    Raises:
        HTTPException: 404 if not found, 403 if access denied
    """
    # Single query: find controller that belongs to this user
    controller = db.query(Controller).filter(
        Controller.id == controller_id,
        Controller.user_id == user.id
    ).first()

    if not controller:
        # Check if controller exists at all to give appropriate error message
        controller_exists = db.query(Controller).filter(Controller.id == controller_id).first()
        if not controller_exists:
            raise HTTPException(status_code=404, detail=f"Controller with ID {controller_id} not found.")
        else:
            raise HTTPException(status_code=403, detail=f"Access denied to controller {controller_id}.")

    return controller


def get_dynamic_r1_client(
    controller_id: int = Path(..., description="Controller ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> R1Client:
    """
    Get an R1Client for a specific controller ID with proper authorization checks.
    This is the main dependency for the new dynamic routing system.

    Args:
        controller_id: Database primary key of the controller
        current_user: Current authenticated user
        db: Database session

    Returns:
        R1Client configured for the controller

    Raises:
        HTTPException: If access denied, not found, or authentication fails
    """
    print(f"get_dynamic_r1_client - Fetching R1Client for controller: {controller_id}, user: {current_user.email}")

    # Validate controller access
    controller = validate_controller_access(controller_id, current_user, db)

    # Verify it's a RuckusONE controller
    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"This endpoint requires a RuckusONE controller. Controller '{controller.name}' (ID: {controller_id}) is a {controller.controller_type} controller. Please select a RuckusONE controller."
        )

    # Create and return the R1Client
    try:
        client = create_r1_client_from_controller(controller.id, db)

        # Check if authentication failed
        if getattr(client, "auth_failed", False):
            raise HTTPException(status_code=401, detail="R1Client authentication failed")

        print(f"Successfully created R1Client for controller: {controller_id}")
        return client

    except Exception as e:
        print(f"Error creating R1Client for controller {controller_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create R1Client for controller {controller_id}: {str(e)}")

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

def get_controller_aware_r1_client(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)) -> R1Client:
    """
    Extract controller_id from the request path and return appropriate R1Client.
    This allows existing endpoints to work without modification.

    Path format: /r1/{controller_id}/...
    """
    path_parts = request.url.path.strip('/').split('/')

    # Find controller_id in path (should be after 'r1')
    try:
        r1_index = path_parts.index('r1')
        if r1_index + 1 < len(path_parts):
            controller_id = int(path_parts[r1_index + 1])
            return get_dynamic_r1_client(controller_id, current_user, db)
    except (ValueError, IndexError):
        pass

    raise HTTPException(status_code=400, detail="Could not extract controller_id from request path")
