from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Increase pool size for parallel workloads
# Default was pool_size=5, max_overflow=10 (15 max connections)
# Parallel imports + webhooks can easily exceed this
engine = create_engine(
    DATABASE_URL,
    pool_size=10,           # Base connections kept open
    max_overflow=20,        # Additional connections when pool is full
    pool_timeout=60,        # Wait up to 60s for a connection
    pool_pre_ping=True,     # Check connections are alive before using
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
