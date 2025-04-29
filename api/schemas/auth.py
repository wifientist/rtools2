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
    role: str = "user"  # Default role

class UserCreate(UserBase):
    email: EmailStr

class UserResponse(UserBase):
    id: int
    email: EmailStr  # Ensure email is returned

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
