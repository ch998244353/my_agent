from __future__ import annotations


__all__ = ["fast_power"]


def fast_power(base: int, exp: int, mod: int | None = None) -> int:
    """Compute base ** exp using binary exponentiation, optionally modulo mod."""
    if mod == 1:
        return 0
    if exp == 0:
        return 1 if mod is None else 1 % mod
    result = 1
    while exp > 0:
        if exp & 1:
            result = result * base
            if mod is not None:
                result %= mod
        base = base * base
        if mod is not None:
            base %= mod
        exp >>= 1
    return result
