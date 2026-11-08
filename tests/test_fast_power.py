from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.fast_power import fast_power  # noqa: E402


@pytest.mark.parametrize(
    ("base", "exp", "expected"),
    [
        (2, 10, 1024),
        (3, 5, 243),
        (5, 0, 1),
        (0, 5, 0),
        (0, 0, 1),
        (-2, 3, -8),
        (-2, 4, 16),
    ],
)
def test_fast_power_basic_correctness(
    base: int, exp: int, expected: int
) -> None:
    assert fast_power(base, exp) == base**exp
    assert fast_power(base, exp) == expected


@pytest.mark.parametrize(
    ("base", "exp", "mod"),
    [
        (2, 10, 3),
        (3, 5, 7),
        (5, 0, 1),
        (0, 5, 7),
        (0, 0, 1),
        (-3, 3, 7),
        (-2, 4, 10),
        (3, 10**9, 1000000007),
    ],
)
def test_fast_power_mod_matches_pow(base: int, exp: int, mod: int) -> None:
    assert fast_power(base, exp, mod) == pow(base, exp, mod)


def test_fast_power_mod_one_returns_zero() -> None:
    assert fast_power(12345, 100, 1) == 0
    assert fast_power(0, 0, 1) == 0


def test_fast_power_zero_exponent_returns_one_mod_mod() -> None:
    assert fast_power(7, 0) == 1
    assert fast_power(7, 0, 5) == 1 % 5
    assert fast_power(7, 0, 1) == 0


def test_fast_power_negative_base_modulo() -> None:
    assert fast_power(-3, 3, 7) == (-27) % 7
    assert fast_power(-3, 3, 7) == pow(-3, 3, 7)


def test_fast_power_large_exponent_modulo() -> None:
    base, exp, mod = 3, 10**9, 1000000007
    assert fast_power(base, exp, mod) == pow(base, exp, mod)


def test_fast_power_randomized_matches_pow() -> None:
    rng = random.Random(20240401)
    for _ in range(200):
        base = rng.randrange(0, 1000)
        exp = rng.randrange(0, 100)
        mod = rng.randrange(1, 1000)
        assert fast_power(base, exp) == pow(base, exp)
        assert fast_power(base, exp, mod) == pow(base, exp, mod)


def test_fast_power_constant_one_is_fast() -> None:
    assert fast_power(1, 10**6) == 1


def test_fast_power_large_exponent_large_prime() -> None:
    assert fast_power(2, 10**6, 10**9 + 7) == pow(2, 10**6, 10**9 + 7)
