"""
Lightweight Result type — no exceptions, just values.

Inspired by Rust's Result<T, E>.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, Union

T = TypeVar("T")
E = TypeVar("E")


class ErrorSeverity(str, enum.Enum):
    """Severity levels for RPC errors."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(slots=True)
class RPCError:
    """Structured error from an RPC or infrastructure operation."""

    code: str
    message: str
    severity: ErrorSeverity = ErrorSeverity.MEDIUM
    source: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass(slots=True)
class Ok(Generic[T]):
    """Success variant of Result."""

    value: T
    is_ok: bool = field(default=True, init=False, repr=False)
    is_err: bool = field(default=False, init=False, repr=False)

    def unwrap(self) -> T:
        return self.value

    def unwrap_err(self) -> Any:
        raise ValueError("Called unwrap_err on Ok")


@dataclass(slots=True)
class Err(Generic[E]):
    """Error variant of Result."""

    error: E
    is_ok: bool = field(default=False, init=False, repr=False)
    is_err: bool = field(default=True, init=False, repr=False)

    def unwrap(self) -> Any:
        raise ValueError(f"Called unwrap on Err: {self.error}")

    def unwrap_err(self) -> E:
        return self.error


Result = Union[Ok[T], Err[E]]
