from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine
import models
from routers import status, proposals, bids, users, auth, protected, company
import os
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()

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
models.Base.metadata.create_all(bind=engine)

# 🚀 Include Routers
app.include_router(status.router)
app.include_router(users.router)
app.include_router(auth.router, tags=["Authentication"])
app.include_router(company.router)
app.include_router(protected.router, tags=["Protected"])
app.include_router(proposals.router)
app.include_router(bids.router)


# from fastapi.routing import APIRoute
# for route in app.routes:
#     print(route.path)
