from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Increase pool size for parallel workloads
# Default was pool_size=5, max_overflow=10 (15 max connections)
# Parallel imports + webhooks + SSE fallback polling can easily exceed this
# The revocation cache in security.py reduces DB hits significantly,
# but we still need headroom for concurrent authenticated requests
engine = create_engine(
    DATABASE_URL,
    pool_size=15,           # Base connections kept open (was 10)
    max_overflow=25,        # Additional connections when pool is full (was 20)
    pool_timeout=30,        # Fail faster so frontend can retry (was 60)
    pool_pre_ping=True,     # Check connections are alive before using
    pool_recycle=1800,      # Recycle connections every 30 min to avoid stale
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
