from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from models.user import User

# from clients.r1_client import get_scoped_r1_client
from clients.r1_client import create_r1_client_from_tenant
from r1api.client import R1Client

from dependencies import get_db
from dependencies import get_current_user

router = APIRouter(
    prefix="/ec",
    tags=["fer1agg"],
)

@router.get("/active")
async def get_active_ec(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
): 
    """
    Fetches either the EC list (if MSP) or just the EC itself (if not MSP) from the R1 client.
    """    
    # r1_client = await get_scoped_r1_client("a")
    # ecs = await r1_client.msp.get_msp_ecs()
    tenant_id = current_user.active_tenant_id
    r1_client = create_r1_client_from_tenant(tenant_id, db)

    ecs = await r1_client.msp.get_msp_ecs()
    if not ecs:
        # If no ECS are found, return an empty list
        ecs = []
    if ecs.get('success') == False:
        tenants_self = await r1_client.tenant.get_tenant_self()
        tenants_user_profiles = await r1_client.tenant.get_tenant_user_profiles()
        raw_ecs = {"self": tenants_self, "userProfiles": tenants_user_profiles}
        print(raw_ecs)
        ecs = extract_ec_list(raw_ecs.get("ecs", {}))
    
    answer =  {
         "ecs": ecs,
    }
    print(answer)
    return {'status': 'success', 'data': answer}

@router.get("/secondary")
async def get_secondary_ec(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
): 
    """
    Fetches either the EC list (if MSP) or just the EC itself (if not MSP) from the R1 client.
    """    
    # r1_client = await get_scoped_r1_client("a")
    # ecs = await r1_client.msp.get_msp_ecs()
    tenant_id = current_user.secondary_tenant_id
    r1_client = create_r1_client_from_tenant(tenant_id, db)

    ecs = await r1_client.msp.get_msp_ecs()
    if not ecs:
        # If no ECS are found, return an empty list
        ecs = []
    if ecs.get('success') == False:
        tenants_self = await r1_client.tenant.get_tenant_self()
        tenants_user_profiles = await r1_client.tenant.get_tenant_user_profiles()
        raw_ecs = {"self": tenants_self, "userProfiles": tenants_user_profiles}
        print(raw_ecs)
        ecs = extract_ec_list(raw_ecs.get("ecs", {}))

    answer =  {
         "ecs": ecs,
    }
    print(answer)
    return {'status': 'success', 'data': answer}

@router.get("/dual")
async def get_active_and_secondary_ecs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
): 
    async def build_ec_response(tenant_id: int) -> dict:
        if not tenant_id:
            return None

        client = create_r1_client_from_tenant(tenant_id, db)

        result = {
            "ecs": None,
            "self": None,
            "userProfiles": None,
        }

        msp_ecs = await client.msp.get_msp_ecs()
        print("MSP ECS Response:", msp_ecs)
        if msp_ecs:
            if msp_ecs.get("data"):
                result["ecs"] = extract_ec_list(msp_ecs)
        
        result["self"] = await client.tenant.get_tenant_self()
        result["userProfiles"] = await client.tenant.get_tenant_user_profiles()

        return result

    active_data = await build_ec_response(current_user.active_tenant_id)
    secondary_data = await build_ec_response(current_user.secondary_tenant_id)

    print("Active EC Data:", active_data)
    print("Secondary EC Data:", secondary_data)

    return {
        "status": "success",
        "data": {
            "active": active_data,
            "secondary": secondary_data
        }
    }


def extract_ec_list(raw_ecs: dict) -> list[dict]:
    if "data" in raw_ecs:  # MSP-style
        return [{"id": ec["id"], "name": ec["name"], "tenantType": ec['tenantType']} for ec in raw_ecs["data"]]
    elif "self" in raw_ecs:  # VAR-style
        ec = raw_ecs["self"]
        return [{"id": ec["id"], "name": ec["name"]}]
    else:
        return []
