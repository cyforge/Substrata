"""Validation helpers shared by Substrata's domain objects."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import Enum
from ipaddress import ip_address
from typing import TypeAlias, TypeVar
from urllib.parse import urlsplit

from .errors import ValidationError

AmountInput: TypeAlias = str | int | Decimal
EnumValue = TypeVar("EnumValue", bound=Enum)


def validate_required_string(value: str, *, field_name: str) -> str:
    """Return non-blank string unchanged or raise a field-specific error."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")
    return value


def normalize_origin(value: str) -> str:
    """Validate and canonicalize an HTTP(S) origin for allowlist comparisons."""
    validate_required_string(value, field_name="Origin")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValidationError("Origin cannot contain whitespace or control characters")
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("Origin is malformed") from exc
    if scheme not in {"http", "https"} or hostname is None:
        raise ValidationError("Origin must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError("Origin cannot include credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValidationError("Origin cannot include a path, query, or fragment")
    if parsed.netloc.endswith(":"):
        raise ValidationError("Origin port cannot be empty")
    if "%" in hostname:
        raise ValidationError("Origin host cannot include an IPv6 zone identifier")
    try:
        host = ip_address(hostname).compressed
    except ValueError:
        try:
            if hostname.endswith(".."):
                raise ValidationError("Origin host has repeated trailing dots")
            dns_name = hostname[:-1] if hostname.endswith(".") else hostname
            host = dns_name.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValidationError("Origin host is invalid") from exc
        if (
            not host
            or len(host) > 253
            or any(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                for label in host.split(".")
            )
        ):
            raise ValidationError("Origin host is invalid") from None
    if ":" in host:
        host = f"[{host}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    port_suffix = f":{port}" if port is not None and not default_port else ""
    return f"{scheme}://{host}{port_suffix}"


def validate_enum(
    value: EnumValue | str, enum_type: type[EnumValue], *, field_name: str
) -> EnumValue:
    """Parse enum member or its value and report invalid values uniformly."""
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (ValueError, TypeError) as exc:
        choices = ", ".join(repr(member.value) for member in enum_type)
        raise ValidationError(f"{field_name} must be one of: {choices}") from exc


def validate_amount(value: AmountInput, *, positive: bool = True) -> Decimal:
    """Return exact monetary value; reject floats and values beyond Stellar precision."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValidationError("Amount must be supplied as str, int, or Decimal")
    if not isinstance(value, (str, int, Decimal)):
        raise ValidationError("Amount must be supplied as str, int, or Decimal")
    if isinstance(value, str) and len(value) > 128:
        raise ValidationError("Amount text exceeds supported size")
    if isinstance(value, int) and value.bit_length() > 128:
        raise ValidationError("Amount integer exceeds supported size")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Amount is not a valid decimal") from exc
    if not amount.is_finite():
        raise ValidationError("Amount must be finite")
    parts = amount.as_tuple()
    if abs(parts.exponent) > 38 or len(parts.digits) > 38 or amount.adjusted() >= 38:
        raise ValidationError("Amount exceeds supported decimal precision or magnitude")
    if positive and amount <= 0:
        raise ValidationError("Amount must be greater than zero")
    if not positive and amount < 0:
        raise ValidationError("Amount cannot be negative")
    if max(0, -amount.as_tuple().exponent) > 7:
        raise ValidationError("Amount cannot have more than 7 fractional digits")
    return amount


def amount_to_atomic_units(amount: Decimal) -> int:
    """Convert validated seven-place decimal amount to exact integer stroops."""
    normalized = validate_amount(amount, positive=False)
    parts = normalized.as_tuple()
    coefficient = int("".join(map(str, parts.digits)) or "0")
    units = coefficient * 10 ** (parts.exponent + 7)
    return -units if parts.sign else units
