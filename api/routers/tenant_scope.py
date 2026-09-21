"""
Which tenant an R1 request is scoped to, on MSP and direct-EC controllers.

THE BUG THIS EXISTS TO PREVENT

RuckusONE keys come in two shapes. A direct EC key belongs to one tenant and
needs nothing extra. An MSP key belongs to the MSP and DELEGATES to a chosen
customer per request, via an override-tenant header.

Both shapes are stored the same way, with an `r1_tenant_id` on the controller
row -- and on an MSP that column holds the MSP'S OWN tenant id. It is a real,
queryable scope: it answers, it authenticates, and it contains the MSP's own
handful of venues. Which made this the most expensive line in the codebase:

    tenant_id = request.tenant_id or controller.r1_tenant_id

On an MSP with no EC chosen it silently answered against the MSP instead of
the customer. Venues were listed from the EC, the work ran against the MSP,
and the result was not an error -- it was an empty venue. No APs, no SSIDs,
no DPSK services, and nothing anywhere saying why.

Worse, the guard meant to catch this,

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(400, "tenant_id is required for MSP controllers")

sat AFTER the fallback and so could never fire. The check was written, and
reading the code it looked handled.

Verified 2026-09-20 on a live MSP: the MSP's own tenant returned 3 venues,
a delegated EC returned 8, and no error was raised either way.

USE resolve_tenant_id() ANYWHERE A REQUEST PICKS A TENANT.
"""

from typing import Any, Optional

from fastapi import HTTPException


def resolve_tenant_id(controller: Any, requested: Optional[str]) -> Optional[str]:
    """
    The tenant every R1 call for this request must be scoped to.

    On MSP the EC is required and never inferred -- there is no safe default,
    because the plausible-looking one silently answers about the wrong
    company. On a direct EC controller the key already belongs to the tenant
    and r1_tenant_id is the right and only answer.

    Raises HTTPException(400) on an MSP controller with no EC selected.
    """
    if getattr(controller, "controller_subtype", None) == "MSP":
        if not requested:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This is an MSP controller, so an MSP-EC (tenant) must be "
                    "selected — the MSP's own tenant holds none of your "
                    "customers' venues, APs or DPSK services."
                ),
            )
        return requested
    return requested or getattr(controller, "r1_tenant_id", None)
