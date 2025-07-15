from pydantic import BaseModel #, IPvAnyAddress
from typing import List

class Option43Request(BaseModel):
    vendor: str
    ip_list: List[str]  #IPvAnyAddress (if you don't want fqdn)

class Option43Response(BaseModel):
    option_43_hex: str
    note: str