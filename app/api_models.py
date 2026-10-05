"""Validated HTTP request models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.validation import UrlValidationError, normalize_website_url


class WebsiteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    enabled: bool = True
    interval_seconds: int | None = Field(default=None, ge=5, le=86400)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("name must not be empty")
        if any(ord(character) < 32 for character in clean):
            raise ValueError("name must not contain control characters")
        return clean

    @field_validator("url")
    @classmethod
    def clean_url(cls, value: str) -> str:
        try:
            return normalize_website_url(value)
        except UrlValidationError as exc:
            raise ValueError(str(exc)) from exc


class WebsiteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=5, le=86400)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        clean = value.strip()
        if not clean:
            raise ValueError("name must not be empty")
        if any(ord(character) < 32 for character in clean):
            raise ValueError("name must not contain control characters")
        return clean

    @field_validator("url")
    @classmethod
    def clean_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            return normalize_website_url(value)
        except UrlValidationError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def validate_patch(self) -> WebsiteUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)
