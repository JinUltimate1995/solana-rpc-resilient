"""Tests for the Result type."""

from solana_rpc_resilient import Err, Ok, RPCError
from solana_rpc_resilient.types import ErrorSeverity


def test_ok_unwrap():
    result = Ok(42)
    assert result.is_ok
    assert not result.is_err
    assert result.unwrap() == 42


def test_err_unwrap_err():
    err = RPCError(code="TEST", message="fail")
    result = Err(err)
    assert result.is_err
    assert not result.is_ok
    assert result.unwrap_err() is err


def test_ok_unwrap_err_raises():
    result = Ok(42)
    try:
        result.unwrap_err()
        assert False, "Should have raised"
    except ValueError:
        pass


def test_err_unwrap_raises():
    result = Err(RPCError(code="X", message="bad"))
    try:
        result.unwrap()
        assert False, "Should have raised"
    except ValueError:
        pass


def test_rpc_error_str():
    err = RPCError(code="TIMEOUT", message="timed out")
    assert str(err) == "[TIMEOUT] timed out"


def test_rpc_error_severity_default():
    err = RPCError(code="X", message="x")
    assert err.severity == ErrorSeverity.MEDIUM
