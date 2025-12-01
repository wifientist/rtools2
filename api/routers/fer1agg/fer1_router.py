from fastapi import APIRouter, Depends

from .msp import router as msp_router
from .venues import router as venues_router
from .networks import router as networks_router
from .tenant import router as tenant_router
from .ec import router as ec_router
from .speed_context import router as speed_context_router
from .speed_explainer import router as speed_explainer_router

# router = APIRouter(
#     prefix="/fer1agg",
# )

# # Mount your sub-routers
# router.include_router(msp_router)
# router.include_router(venues_router)
# router.include_router(networks_router)
# router.include_router(tenant_router)
# router.include_router(ec_router)

from clients.r1_client import get_dynamic_r1_client #, get_scoped_r1_client

subrouters = [msp_router, venues_router, networks_router, tenant_router, speed_context_router, speed_explainer_router]

# def create_fer1_router(scope: str) -> APIRouter:
#     prefix = f"/fer1agg{scope}"
#     client_selector = "active" if scope == "a" else "secondary"
#     router = APIRouter(prefix=prefix)

#     for sub in subrouters:
#         router.include_router(sub, dependencies=[Depends(get_scoped_r1_client(client_selector))])

#     return router

def create_dynamic_fer1_router() -> APIRouter:
    prefix = "/fer1agg/{controller_id}"
    router = APIRouter(prefix=prefix)

    for sub in subrouters:
        router.include_router(sub, dependencies=[Depends(get_dynamic_r1_client)])

    return router

# fe_router_a = create_fer1_router("a")  # /fer1agga
# fe_router_b = create_fer1_router("b")  # /fer1aggb

dynamic_fe_router = create_dynamic_fer1_router()  # /fer1agg/{controller_id}

feagg_ec_router = APIRouter(prefix="/fer1agg")
feagg_ec_router.include_router(ec_router)
