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
#from routers.r1.r1_router import router as r1_router
from routers.r1.r1_router import router_a, router_b
#from routers.fer1agg.fer1_router import router as fer1agg_router
from routers.fer1agg.fer1_router import fe_router_a, fe_router_b

import os
from dotenv import load_dotenv
load_dotenv()

app = FastAPI(
    title="Ruckus.Tools API",  # Add a proper title
    version="1.0.2",
    openapi_version="3.1.0",  # Explicitly set OpenAPI version
    description="Backend API endpoints for the ruckus tools ecosystem",  # Optional but recommended
    root_path="/api"
)
#app = FastAPI()

# Add this middleware to auto-detect the root path
# @app.middleware("http")
# async def add_root_path(request: Request, call_next):
#     # If behind a proxy with X-Forwarded-Prefix header
#     forwarded_prefix = request.headers.get("X-Forwarded-Prefix")
#     if forwarded_prefix:
#         request.scope["root_path"] = forwarded_prefix
#     response = await call_next(request)
#     return response

origins = os.getenv("CORS_ORIGINS", "*").split(",")

# 🚀 Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'], #origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure tables exist
models.user.Base.metadata.create_all(bind=engine)

# 🚀 Include Routers
app.include_router(status.router)
app.include_router(opt43.router)
app.include_router(users.router)
app.include_router(tenants.router)
app.include_router(auth.router, tags=["Authentication"])
app.include_router(company.router)
app.include_router(protected.router, tags=["Protected"])
#app.include_router(r1_router)
app.include_router(router_a)
app.include_router(router_b)
#app.include_router(fer1agg_router)
app.include_router(fe_router_a)
app.include_router(fe_router_b)

# Debug: Print all routes to check for conflicts
print("=== ALL ROUTES ===")
for route in app.routes:
    print(f"Path: {route.path}")
print("=== END ROUTES ===")

# 🔥 Handle normal FastAPI HTTPExceptions (like 404, 400, 401, etc.)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )

# 🔥 Handle request validation errors (e.g., invalid payload schema)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "details": exc.errors()},
    )

# 🔥 Handle unexpected server errors (500s, coding bugs)
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    print("Unexpected error:", traceback.format_exc())  # 📋 Optional: log full traceback in console
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )