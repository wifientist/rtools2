from pydantic import BaseModel, EmailStr

class RequestOtpSchema(BaseModel):
    email: EmailStr

class LoginOtpSchema(BaseModel):
    email: EmailStr
    otp_code: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str

class UserBase(BaseModel):
    email: EmailStr

class UserCreate(UserBase):
    email: EmailStr
    role: str | None = None  # Optional: Only used by admin endpoints
    beta_enabled: bool | None = None  # Optional: Only used by admin endpoints
    # Note: For public signup, role is NOT accepted from client input
    # New users from public signup are always assigned "user" role by default

class UserResponse(UserBase):
    id: int
    email: EmailStr
    role: str
    beta_enabled: bool
    company_id: int | None

    class Config:
        from_attributes = True  # Enables ORM conversion

class CompanyBase(BaseModel):
    name: str

class CompanyCreate(CompanyBase):
    pass

class CompanyResponse(CompanyBase):
    id: int

    class Config:
        from_attributes = True
