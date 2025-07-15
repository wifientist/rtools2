# # api/routers/r1/r1_router.py

# from fastapi import APIRouter
# from .msp_router import router as msp_router
# from .venues_router import router as venues_router
# from .networks_router import router as networks_router
# from .tenant_router import router as tenant_router
# #from .security_router import router as security_router
# #from .services_router import router as services_router

# router = APIRouter(
#     prefix="/r1",
# )

# # Mount your sub-routers
# router.include_router(msp_router)
# router.include_router(venues_router)
# router.include_router(networks_router)
# router.include_router(tenant_router)
# #router.include_router(security_router)
# #router.include_router(services_router)


### new ###

from fastapi import APIRouter, Depends
from .msp_router import router as msp_router
from .venues_router import router as venues_router
from .networks_router import router as networks_router
from .tenant_router import router as tenant_router
from clients.r1_client import get_scoped_r1_client

subrouters = [msp_router, venues_router, networks_router, tenant_router]

def create_r1_router(scope: str) -> APIRouter:
    prefix = f"/r1{scope}"
    client_selector = "active" if scope == "a" else "secondary"
    router = APIRouter(prefix=prefix)

    for sub in subrouters:
        router.include_router(sub, dependencies=[Depends(get_scoped_r1_client(client_selector))])

    return router

router_a = create_r1_router("a")  # /r1a
router_b = create_r1_router("b")  # /r1b


