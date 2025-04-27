from pydantic import BaseModel, EmailStr
from datetime import datetime

### 🚀 Token Response
class TokenResponse(BaseModel):
    access_token: str
    token_type: str


### 🚀 User Schemas
class UserBase(BaseModel):
    email: EmailStr
    role: str = "user"  # Default role

class UserCreate(UserBase):
    email: EmailStr

class UserResponse(UserBase):
    id: int
    email: EmailStr  # Ensure email is returned

    class Config:
        from_attributes = True  # Enables ORM conversion

# class UserAuth(BaseModel):
#     email: EmailStr
#     password: str


class CompanyBase(BaseModel):
    name: str

class CompanyCreate(CompanyBase):
    pass

class CompanyResponse(CompanyBase):
    id: int

    class Config:
        from_attributes = True


### 🚀 Proposal Schemas
class ProposalBase(BaseModel):
    title: str
    description: str
    budget: float
    location: str
    deadline: datetime

class ProposalCreate(ProposalBase):
    title: str
    description: str
    budget: float
    location: str
    deadline: datetime

class ProposalResponse(ProposalBase):
    id: int
    created_by: int

    class Config:
        from_attributes = True

### 🚀 Bid Schemas
class BidBase(BaseModel):
    proposal_id: int
    amount: float
    message: str
    bidder_id: int
    submitted_at: datetime

class BidCreate(BidBase):
    pass

class BidResponse(BidBase):
    id: int

    class Config:
        from_attributes = True
