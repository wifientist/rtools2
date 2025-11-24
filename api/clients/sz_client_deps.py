"""
SmartZone Client Dependency Injection

Provides FastAPI dependency functions for creating SmartZone clients
with proper authentication and authorization.
"""

from fastapi import HTTPException, Depends, Path
from sqlalchemy.orm import Session
from dependencies import get_db, get_current_user
from models.user import User
from models.controller import Controller
from szapi.client import SZClient


def create_sz_client_from_controller(controller_id: int, db: Session) -> SZClient:
    """
    Create a SZClient using credentials from a Controller record.
    Only works for SmartZone controllers.

    Args:
        controller_id: Database primary key of the controller
        db: Database session

    Returns:
        SZClient configured with controller credentials

    Raises:
        HTTPException: If controller not found or not SmartZone type
    """
    print(f"create_sz_client_from_controller - Fetching controller with ID: {controller_id}")

    controller = db.query(Controller).filter(Controller.id == controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail=f"Controller with ID {controller_id} not found.")

    # Verify this is a SmartZone controller
    if controller.controller_type != "SmartZone":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create SmartZone client for {controller.controller_type} controller. Only SmartZone controllers supported."
        )

    # Decrypt credentials
    try:
        username = controller.get_sz_username()
        password = controller.get_sz_password()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error decrypting SmartZone credentials: {str(e)}")

    # Validate required fields
    if not controller.sz_host:
        raise HTTPException(status_code=400, detail="SmartZone host not configured for this controller")

    if not username or not password:
        raise HTTPException(status_code=400, detail="SmartZone credentials not configured for this controller")

    # Create SmartZone client
    # Use the sz_version field as API version (e.g., v11_1, v12_0, v13_0)
    api_version = controller.sz_version if controller.sz_version else "v12_0"

    return SZClient(
        host=controller.sz_host,
        username=username,
        password=password,
        port=controller.sz_port or 8443,
        use_https=controller.sz_use_https if controller.sz_use_https is not None else True,
        verify_ssl=False,  # Most SmartZone deployments use self-signed certs
        api_version=api_version
    )


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


def get_dynamic_sz_client(
    controller_id: int = Path(..., description="Controller ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SZClient:
    """
    Get a SZClient for a specific controller ID with proper authorization checks.
    This is the main dependency for SmartZone endpoints.

    Args:
        controller_id: Database primary key of the controller
        current_user: Current authenticated user
        db: Database session

    Returns:
        SZClient configured for the controller

    Raises:
        HTTPException: If access denied, not found, or authentication fails
    """
    print(f"get_dynamic_sz_client - Fetching SmartZone client for controller: {controller_id}, user: {current_user.email}")

    # Validate controller access
    controller = validate_controller_access(controller_id, current_user, db)

    # Verify it's a SmartZone controller
    if controller.controller_type != "SmartZone":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create SmartZone client for {controller.controller_type} controller."
        )

    # Create and return the SmartZone client
    try:
        client = create_sz_client_from_controller(controller.id, db)
        print(f"Successfully created SmartZone client for controller: {controller_id}")
        return client

    except Exception as e:
        print(f"Error creating SmartZone client for controller {controller_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create SmartZone client for controller {controller_id}: {str(e)}"
        )


def get_sz_active_client(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SZClient:
    """
    Get a SZClient using the user's active controller.

    Args:
        current_user: Current authenticated user
        db: Database session

    Returns:
        SZClient for active controller

    Raises:
        HTTPException: If no active controller or not SmartZone type
    """
    print("get_sz_active_client - Fetching SmartZone client for user:", current_user.email)

    controller_id = current_user.active_controller_id
    if controller_id is None:
        raise HTTPException(status_code=400, detail="No active controller selected.")

    controller = db.query(Controller).filter(Controller.id == controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail="Active controller not found.")

    if controller.controller_type != "SmartZone":
        raise HTTPException(
            status_code=400,
            detail=f"Active controller is {controller.controller_type}, not SmartZone."
        )

    return create_sz_client_from_controller(controller_id, db)
