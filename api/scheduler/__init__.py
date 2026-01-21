"""
Generic Scheduler Service package.

This package provides a reusable scheduler service that any feature can use
to register and execute scheduled jobs.
"""
from scheduler.service import SchedulerService, get_scheduler

__all__ = ['SchedulerService', 'get_scheduler']
