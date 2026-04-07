from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
import os
import logging
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

# Pool settings are configurable via env vars so each worker process
# can be tuned independently.  With N workers, total max DB connections
# = (DB_POOL_SIZE + DB_MAX_OVERFLOW) * N, so keep these values low
# enough that the product stays under PostgreSQL's max_connections.
engine = create_engine(
    DATABASE_URL,
    pool_size=int(os.getenv("DB_POOL_SIZE", "15")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "25")),
    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    pool_pre_ping=True,     # Check connections are alive before using
    pool_recycle=1800,      # Recycle connections every 30 min to avoid stale
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------------------------
# Pool health logging
# ---------------------------------------------------------------------------

@event.listens_for(engine, "checkout")
def _on_checkout(dbapi_conn, connection_rec, connection_proxy):
    """Warn when the connection pool is approaching exhaustion."""
    pool = engine.pool
    overflow = pool.overflow()
    max_overflow = pool._max_overflow
    if max_overflow > 0 and overflow > max_overflow * 0.8:
        logger.warning(
            "DB pool nearing exhaustion: overflow=%s/%s (checked-out=%s, pool_size=%s)",
            overflow, max_overflow, pool.checkedout(), pool.size(),
        )


@event.listens_for(engine, "connect")
def _on_connect(dbapi_conn, connection_rec):
    """Log when the pool opens a brand-new database connection."""
    pool = engine.pool
    logger.info(
        "DB pool new connection opened (pool_size=%s, checked-out=%s, overflow=%s)",
        pool.size(), pool.checkedout(), pool.overflow(),
    )
