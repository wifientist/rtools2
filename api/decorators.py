from functools import wraps
from fastapi import HTTPException, status
from models.user import User, RoleEnum


def require_role(required_role: RoleEnum):
    """
    Decorator to enforce role-based access control.

    Usage:
        @router.get("/admin/companies")
        @require_role(RoleEnum.admin)
        def list_companies(current_user: User = Depends(get_current_user)):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, current_user: User = None, **kwargs):
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )

            # Role hierarchy check
            from constants.roles import role_hierarchy
            user_level = role_hierarchy.get(current_user.role.value, 0)
            required_level = role_hierarchy.get(required_role.value, 999)

            if user_level < required_level:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Requires {required_role.value} role or higher"
                )

            return await func(*args, current_user=current_user, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, current_user: User = None, **kwargs):
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )

            # Role hierarchy check
            from constants.roles import role_hierarchy
            user_level = role_hierarchy.get(current_user.role.value, 0)
            required_level = role_hierarchy.get(required_role.value, 999)

            if user_level < required_level:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Requires {required_role.value} role or higher"
                )

            return func(*args, current_user=current_user, **kwargs)

        # Return appropriate wrapper based on whether original function is async
        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
