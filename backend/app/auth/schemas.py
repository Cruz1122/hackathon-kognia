from __future__ import annotations

import re
import unicodedata
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..db.models import UserRole


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid email.")
    normalized = value.strip().casefold()
    if (
        len(normalized) > 320
        or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized)
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError("Invalid email.")
    return normalized


def normalize_slug(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid organization slug.")
    ascii_value = (
        unicodedata.normalize("NFKD", value.strip())
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    if not slug or len(slug) > 100:
        raise ValueError("Invalid organization slug.")
    return slug


def normalize_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid organization name.")
    name = " ".join(value.split())
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise ValueError("Invalid organization name.")
    return name


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_name(value)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        return normalize_slug(value)


class AdminCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: UserRole
    organization_id: uuid.UUID | None
    is_active: bool


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse

