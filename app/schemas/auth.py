import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator


def _check_password(value: str) -> str:
    if len(value.strip()) < 8:
        raise ValueError("Password must not be whitespace only or shorter than 8 characters")
    return value


NewPassword = Annotated[
    str,
    Field(min_length=8, max_length=128, description="At least 8 characters"),
    AfterValidator(_check_password),
]
OpaqueToken = Annotated[str, Field(min_length=16, max_length=512)]


class UserBase(BaseModel):
    email: EmailStr

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, v: EmailStr) -> str:
        return str(v).strip().lower()


class UserCreate(UserBase):
    password: NewPassword


class UserLogin(UserBase):
    password: str = Field(..., min_length=1, max_length=128)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    email_verified: bool
    created_at: datetime
    updated_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Lifetime of the access token in seconds")
    refresh_token: str = Field(
        description="Single-use token for POST /auth/refresh; each use returns a new one"
    )


class RefreshRequest(BaseModel):
    refresh_token: OpaqueToken


class EmailRequest(UserBase):
    pass


class EmailTokenRequest(BaseModel):
    token: OpaqueToken


class PasswordResetConfirm(BaseModel):
    token: OpaqueToken
    new_password: NewPassword


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: NewPassword


class AccountDelete(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)


class Message(BaseModel):
    detail: str
