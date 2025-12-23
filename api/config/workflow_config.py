"""
Workflow Engine Configuration

Environment-based configuration for workflow execution
"""

import os


class WorkflowConfig:
    """Workflow engine configuration settings"""

    # Redis Settings
    REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
    REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
    REDIS_DB = int(os.getenv('REDIS_DB', 1))  # DB 1 = workflow state
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)

    # Async Polling Settings
    ASYNC_POLL_INTERVAL = int(os.getenv('ASYNC_POLL_INTERVAL', 3))  # Seconds between polls
    ASYNC_MAX_ATTEMPTS = int(os.getenv('ASYNC_MAX_ATTEMPTS', 60))   # Max polls per task (3 minutes)
    ASYNC_GLOBAL_TIMEOUT = int(os.getenv('ASYNC_GLOBAL_TIMEOUT', 3600))  # 1 hour

    # Parallelization Limits
    MAX_PARALLEL_API_CALLS = int(os.getenv('MAX_PARALLEL_API_CALLS', 50))
    MAX_PARALLEL_POLLS = int(os.getenv('MAX_PARALLEL_POLLS', 100))

    # Retry Settings
    TASK_MAX_RETRIES = int(os.getenv('TASK_MAX_RETRIES', 3))
    TASK_RETRY_BACKOFF = int(os.getenv('TASK_RETRY_BACKOFF', 2))  # Exponential backoff base

    # TTL Settings
    JOB_TTL_DAYS = int(os.getenv('JOB_TTL_DAYS', 7))

    @classmethod
    def get_redis_url(cls) -> str:
        """Get Redis connection URL"""
        if cls.REDIS_PASSWORD:
            return f"redis://:{cls.REDIS_PASSWORD}@{cls.REDIS_HOST}:{cls.REDIS_PORT}/{cls.REDIS_DB}"
        else:
            return f"redis://{cls.REDIS_HOST}:{cls.REDIS_PORT}/{cls.REDIS_DB}"


# Cloudpath Workflow-Specific Settings
class CloudpathConfig:
    """Cloudpath DPSK workflow configuration"""

    MAX_PASSPHRASES_PER_BATCH = int(os.getenv('CLOUDPATH_MAX_PASSPHRASES_PER_BATCH', 100))
    DPSK_POOL_BATCH_SIZE = int(os.getenv('CLOUDPATH_DPSK_POOL_BATCH_SIZE', 50))
    IDENTITY_GROUP_BATCH_SIZE = int(os.getenv('CLOUDPATH_IDENTITY_GROUP_BATCH_SIZE', 20))
