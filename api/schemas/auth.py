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
    # Note: role is NOT accepted from client input for security reasons
    # New users are always assigned "user" role by default on the server side

class UserResponse(UserBase):
    id: int
    email: EmailStr  # Ensure email is returned
    role: str  # Include role in responses

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
