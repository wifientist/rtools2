from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException as StarletteHTTPException
import traceback

from database import engine
import models
from routers import status, users, auth, protected, company, tenants, opt43
# Updated imports for R1 routers
from routers.r1.r1_router import dynamic_router  #, router_a, router_b, # Legacy routers commented out for backward compatibility
# Updated imports for FER1AGG routers (assuming similar pattern)
from routers.fer1agg.fer1_router import dynamic_fe_router, feagg_ec_router #, fe_router_a, fe_router_b, # Legacy routers commented out for backward compatibility

import os
from dotenv import load_dotenv
load_dotenv()

app = FastAPI(
    title="Ruckus.Tools API",
    version="1.0.2",
    openapi_version="3.1.0",
    description="Backend API endpoints for the ruckus tools ecosystem",
    root_path="/api"
)

origins = os.getenv("CORS_ORIGINS", "*").split(",")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],  # origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure tables exist
models.user.Base.metadata.create_all(bind=engine)

# Include Routers
app.include_router(status.router)
app.include_router(opt43.router)
app.include_router(users.router)
app.include_router(tenants.router)
app.include_router(auth.router, tags=["Authentication"])
app.include_router(company.router)
app.include_router(protected.router, tags=["Protected"])

# R1 Routers - Legacy (for backward compatibility)
#app.include_router(router_a, tags=["R1 Legacy - Active"])
#app.include_router(router_b, tags=["R1 Legacy - Secondary"])

# R1 Router - New Dynamic (main implementation)
app.include_router(dynamic_router)

# FER1AGG Routers (legacy)
#app.include_router(fe_router_a, tags=["FER1AGG Legacy - Active"])
#app.include_router(fe_router_b, tags=["FER1AGG Legacy - Secondary"])

# FER1AGG Router - New Dynamic (main implementation)
app.include_router(dynamic_fe_router)

# FER1AGG EC Router - new aggregation endpoint without tenant_pk dependency
app.include_router(feagg_ec_router)

# Debug: Print all routes to check for conflicts
print("=== ALL ROUTES ===")
for route in app.routes:
    print(f"Path: {route.path}")
print("=== END ROUTES ===")

# Exception handlers
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "details": exc.errors()},
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    print("Unexpected error:", traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )