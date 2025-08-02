# api/routers/r1/r1_router.py

from fastapi import APIRouter, Depends
from .msp_router import router as msp_router
from .venues_router import router as venues_router
from .networks_router import router as networks_router
from .tenant_router import router as tenant_router
from clients.r1_client import get_dynamic_r1_client #, get_scoped_r1_client

# List of all sub-routers
subrouters = [msp_router, venues_router, networks_router, tenant_router]

# def create_r1_router(scope: str) -> APIRouter:
#     """Create legacy routers for backward compatibility (/r1a, /r1b)"""
#     prefix = f"/r1{scope}"
#     client_selector = "active" if scope == "a" else "secondary"
#     router = APIRouter(prefix=prefix)

#     for sub in subrouters:
#         router.include_router(sub, dependencies=[Depends(get_scoped_r1_client(client_selector))])

#     return router

def create_dynamic_r1_router() -> APIRouter:
    """
    Create the new dynamic router that uses tenant_id in the path.
    Routes will be: /r1/{tenant_pk}/msp/..., /r1/{tenant_id}/venues/..., etc.
    """
    router = APIRouter(prefix="/r1/{tenant_pk}")
    
    # Add the dynamic R1Client dependency to all sub-routers
    for sub in subrouters:
        router.include_router(sub, dependencies=[Depends(get_dynamic_r1_client)])
    
    return router

# Legacy routers (for backward compatibility)
# router_a = create_r1_router("a")  # /r1a
# router_b = create_r1_router("b")  # /r1b

# New dynamic router
dynamic_router = create_dynamic_r1_router()  # /r1/{tenant_pk}