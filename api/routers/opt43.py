from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, IPvAnyAddress
from typing import List
from schemas.opt43 import Option43Request, Option43Response
import binascii
import ipaddress

router = APIRouter(prefix="/opt43", tags=["opt43"])

def ip_to_hex(ip: str) -> str:
    return binascii.hexlify(ipaddress.IPv4Address(ip).packed).decode().upper()

def build_option43(vendor: str, ip_list: List[str]) -> str:
    if vendor.lower() == "ruckus":
         # Ruckus SmartZone expects ASCII string of IPs/FQDNs separated by semicolons
        ip_string = ';'.join([str(ip) for ip in ip_list]) 
        ascii_hex = ip_string.encode("ascii").hex() #.upper()
        length = len(ip_string) 
        return f'06{length:02X}{ascii_hex}'  # Option 43 subtype 06
    else:
        raise ValueError("Unsupported vendor")

@router.post("/calculate", response_model=Option43Response)
def calculate_option43(req: Option43Request):
    try:
        hex_string = build_option43(req.vendor, req.ip_list)
        return Option43Response(
            option_43_hex=hex_string,
            note=f"Use this hex string in DHCP Option 43 for vendor '{req.vendor}'"
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
