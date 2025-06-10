# api/routers/r1/r1_router.py

from fastapi import APIRouter
from .msp_router import router as msp_router
from .venues_router import router as venues_router
from .networks_router import router as networks_router
from .tenant_router import router as tenant_router
#from .security_router import router as security_router
#from .services_router import router as services_router

router = APIRouter(
    prefix="/r1",
)

# Mount your sub-routers
router.include_router(msp_router)
router.include_router(venues_router)
router.include_router(networks_router)
router.include_router(tenant_router)
#router.include_router(security_router)
#router.include_router(services_router)

