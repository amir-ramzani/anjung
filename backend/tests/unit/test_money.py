import pytest
from app.core.money import Money, allocate, prorate
from hypothesis import given
from hypothesis import strategies as st

amt = st.integers(min_value=-(10**12), max_value=10**12)


@given(amt, amt)
def test_add_sub_roundtrip(a: int, b: int) -> None:
    assert Money(a) + Money(b) - Money(b) == Money(a)


@given(st.integers(0, 10**12), st.integers(1, 31))
def test_prorate_full_month_is_identity(x: int, n: int) -> None:
    assert prorate(Money(x), n, n) == Money(x)


@given(st.integers(-(10**9), 10**9), st.lists(st.integers(0, 100), min_size=1, max_size=8))
def test_allocate_sums_to_total(total: int, weights: list[int]) -> None:
    if sum(weights) == 0:
        return
    parts = allocate(Money(total), weights)
    assert sum(p.amount_minor for p in parts) == total


def test_half_up_rounding() -> None:
    assert Money(1).times_ratio(1, 2) == Money(1)  # 0.5 -> 1
    assert Money(1).times_ratio(1, 3) == Money(0)
    assert Money(-1).times_ratio(1, 2) == Money(-1)
    assert Money(150000).times_ratio(150, 10000) == Money(2250)  # 1.5% late fee in bp


def test_rejects_float_and_currency_mismatch() -> None:
    with pytest.raises(TypeError):
        Money(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money(True)


def test_format() -> None:
    assert Money(150000).format() == "MYR 1,500.00"
    assert Money(-5).format() == "-MYR 0.05"
