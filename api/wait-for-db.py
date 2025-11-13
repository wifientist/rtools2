#!/usr/bin/env python3
"""
Wait for PostgreSQL database to be ready before starting the application.
This script prevents race conditions when the backend starts before the database is ready.
"""

import os
import sys
import time
import psycopg2
from psycopg2 import OperationalError

def wait_for_db(max_retries=30, retry_interval=2):
    """
    Wait for PostgreSQL database to be ready.

    Args:
        max_retries: Maximum number of connection attempts
        retry_interval: Seconds to wait between retries
    """
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set")
        sys.exit(1)

    # Parse connection string for psycopg2
    # Format: postgresql+psycopg2://user:pass@host:port/dbname
    db_url = database_url.replace("postgresql+psycopg2://", "postgresql://")

    print(f"Waiting for database to be ready at {db_url.split('@')[1]}...")

    for attempt in range(1, max_retries + 1):
        try:
            # Attempt to connect
            conn = psycopg2.connect(db_url)
            conn.close()
            print(f"✓ Database is ready! (attempt {attempt}/{max_retries})")
            return True

        except OperationalError as e:
            print(f"⏳ Database not ready yet (attempt {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                time.sleep(retry_interval)
            else:
                print(f"✗ Failed to connect to database after {max_retries} attempts")
                sys.exit(1)

    return False

if __name__ == "__main__":
    wait_for_db()
