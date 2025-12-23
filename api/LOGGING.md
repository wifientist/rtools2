# Logging Configuration Guide

## Overview

The RuckusTools API now has comprehensive logging configuration that ensures all `logger.info()` statements work properly.

## What Was Fixed

### Before
- **No logging configuration** - Python used defaults
- **Default level**: WARNING (only WARNING, ERROR, CRITICAL showed up)
- **INFO/DEBUG**: Silently ignored ❌
- **Had to use print()** to see anything

### After
- **Proper logging config** in `logging_config.py`
- **Default level**: INFO (INFO, WARNING, ERROR, CRITICAL all show up)
- **Structured logging** with proper formatting
- **Module-specific log levels** for fine-grained control

## How to Use Logging

### In Any Module

```python
import logging

# At the top of your file
logger = logging.getLogger(__name__)

# Then use it
logger.debug("Detailed debugging info")      # Only shows if LOG_LEVEL=DEBUG
logger.info("General information")            # Shows by default
logger.warning("Warning message")             # Always shows
logger.error("Error occurred")                # Always shows
logger.critical("Critical failure!")          # Always shows
```

### Log Levels (from most to least verbose)

1. **DEBUG** - Detailed diagnostic info (queries, API requests, etc.)
2. **INFO** - General informational messages (what you use most)
3. **WARNING** - Something unexpected happened but app continues
4. **ERROR** - An error occurred, some functionality failed
5. **CRITICAL** - Severe error, app may crash

## Configuration

### Environment Variable

Set `LOG_LEVEL` in your `.env` or docker-compose:

```bash
# In .env
LOG_LEVEL=INFO    # Default - shows INFO and above
LOG_LEVEL=DEBUG   # Verbose - shows everything
LOG_LEVEL=WARNING # Quiet - only warnings and errors
```

### Per-Module Levels

Edit `logging_config.py` to set different levels for different modules:

```python
"loggers": {
    "routers": {"level": "INFO"},        # Your route handlers
    "workflow": {"level": "DEBUG"},      # Workflow engine (verbose)
    "r1api": {"level": "INFO"},          # R1 API client
    "uvicorn.access": {"level": "WARNING"},  # Reduce HTTP access log noise
    "sqlalchemy": {"level": "WARNING"},  # Only show SQL warnings
}
```

## Best Practices

### ✅ DO

```python
# Use structured logging with context
logger.info(f"Processing {count} items for user {user_id}")

# Log exceptions with traceback
try:
    something()
except Exception as e:
    logger.error(f"Failed to process: {str(e)}")
    import traceback
    traceback.print_exc()

# Use appropriate levels
logger.debug("Entering function calculate_total()")  # Debug
logger.info("Migration started for venue XYZ")       # Info
logger.warning("API rate limit approaching")         # Warning
logger.error("Database connection failed")           # Error
```

### ❌ DON'T

```python
# Don't use print() anymore
print("This is a log message")  # ❌ Use logger.info() instead

# Don't log sensitive data
logger.info(f"Password: {password}")  # ❌ Security risk!

# Don't log in tight loops without throttling
for item in huge_list:
    logger.info(f"Processing {item}")  # ❌ Log spam!
```

## Viewing Logs

### Development (Docker Compose)

```bash
# Follow all backend logs
docker logs -f rtools-backend-dev

# Search logs for specific text
docker logs rtools-backend-dev 2>&1 | grep "DPSK"

# Last 100 lines
docker logs --tail 100 rtools-backend-dev
```

### Production

Logs are sent to stdout and captured by your container orchestrator (Docker, Kubernetes, etc.)

## Troubleshooting

### "My logger.info() still doesn't show up!"

1. **Check LOG_LEVEL**: Make sure it's set to INFO or DEBUG
2. **Restart containers**: `docker-compose -f docker-compose.dev.yml restart backend`
3. **Check logger name**: Make sure you're using `logger = logging.getLogger(__name__)`

### "Too much noise in logs!"

1. **Increase LOG_LEVEL**: Set to WARNING to reduce output
2. **Adjust module levels**: Edit `logging_config.py` to set specific modules to WARNING
3. **Filter logs**: Use `docker logs ... | grep -v "pattern_to_exclude"`

### "I need different formats for different outputs"

Edit `formatters` in `logging_config.py`:

```python
"formatters": {
    "simple": {
        "format": "%(levelname)s - %(message)s"
    },
    "detailed": {
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
    }
}
```

## Migration from print()

**Old code:**
```python
print("Starting migration...")
print(f"Found {count} items")
```

**New code:**
```python
logger.info("Starting migration...")
logger.info(f"Found {count} items")
```

That's it! Just replace `print()` with `logger.info()` and you're done.
