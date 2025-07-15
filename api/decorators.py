# #from functools import wraps
# from client.r1_client import get_r1_clients
# from fastapi import Depends

# def with_r1_client():
#     def decorator(route_func):
#         @wraps(route_func)
#         async def wrapper(tenant_context: str, *args, **kwargs):
#             r1_clients = await get_r1_clients()
#             client = r1_clients["secondary" if tenant_context == "b" else "active"]

#             if getattr(client, "auth_failed", False):
#                 return client.auth_error

#             return await route_func(client, *args, **kwargs)
#         return wrapper
#     return decorator
