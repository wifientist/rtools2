"""
AP Rename Router

Bulk tool for renaming access points based on:
- CSV mapping (serial → new_name)
- Regex find/replace patterns
- Template patterns with variables
"""

from routers.ap_rename.ap_rename_router import router

__all__ = ['router']
