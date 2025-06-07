from fastapi import APIRouter
from .msp import router as msp_router
from .venues import router as venues_router
# from .networks_router import router as networks_router
#from .security_router import router as security_router
#from .services_router import router as services_router

router = APIRouter(
    prefix="/fer1agg",
)

# Mount your sub-routers
router.include_router(msp_router)
router.include_router(venues_router)