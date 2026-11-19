"""Hand-computed cases for ``northstar.common.money`` (ADR 004)."""

from decimal import Decimal, localcontext

import pytest

from northstar.common.exceptions import CurrencyMismatchError
from northstar.common.money import Currency, Money, allocate


def cad(amount: str) -> Money:
    return Money(Decimal(amount), Currency.CAD)


def usd(amount: str) -> Money:
    return Money(Decimal(amount), Currency.USD)


class TestConstruction:
    @pytest.mark.parametrize("bad_amount", [0.1, 1, "1.00", True, None])
    def test_rejects_non_decimal_amounts(self, bad_amount: object) -> None:
        with pytest.raises(TypeError, match=r"decimal\.Decimal"):
            Money(bad_amount, Currency.CAD)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_amount", ["NaN", "sNaN", "Infinity", "-Infinity"])
    def test_rejects_non_finite_amounts(self, bad_amount: str) -> None:
        with pytest.raises(ValueError, match="finite"):
            Money(Decimal(bad_amount), Currency.CAD)

    def test_rejects_plain_string_currency(self) -> None:
        with pytest.raises(TypeError, match="Currency"):
            Money(Decimal("1.00"), "CAD")  # type: ignore[arg-type]

    def test_is_immutable(self) -> None:
        money = cad("1.00")
        with pytest.raises(AttributeError):
            money.amount = Decimal("2.00")  # type: ignore[misc]

    def test_zero(self) -> None:
        assert Money.zero(Currency.USD) == usd("0")


class TestArithmetic:
    def test_decimal_addition_is_exact_where_float_is_not(self) -> None:
        assert 0.1 + 0.2 != 0.3  # the float failure ADR 004 exists to avoid
        assert cad("0.1") + cad("0.2") == cad("0.3")

    def test_subtraction_and_negation(self) -> None:
        assert usd("10.00") - usd("2.51") == usd("7.49")
        assert -usd("2.51") == usd("-2.51")

    def test_multiplication_by_decimal_is_unrounded(self) -> None:
        # 3 shares at 33.333333 USD: 99.999999, rounded only at quantize().
        gross = Money(Decimal("33.333333"), Currency.USD) * Decimal(3)
        assert gross == usd("99.999999")
        assert Decimal(3) * Money(Decimal("33.333333"), Currency.USD) == gross

    def test_mixing_currencies_raises(self) -> None:
        with pytest.raises(CurrencyMismatchError, match="CAD with USD"):
            cad("1.00") + usd("1.00")
        with pytest.raises(CurrencyMismatchError):
            cad("1.00") - usd("1.00")

    @pytest.mark.parametrize("bad_factor", [1.5, Money(Decimal(2), Currency.CAD)])
    def test_unsupported_multiplication_raises_type_error(self, bad_factor: object) -> None:
        with pytest.raises(TypeError):
            cad("1.00") * bad_factor

    def test_adding_a_non_money_value_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            cad("1.00") + Decimal("1.00")

    def test_result_ignores_the_callers_decimal_context(self) -> None:
        # With 3 significant digits in the thread's context, 1234.56 + 0.01
        # would round to 1.23E+3. The explicit money context keeps it exact.
        with localcontext() as ctx:
            ctx.prec = 3
            assert cad("1234.56") + cad("0.01") == cad("1234.57")


class TestQuantize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0.125", "0.12"),  # tie: round to the even neighbour (2)
            ("0.135", "0.14"),  # tie: round to the even neighbour (4)
            ("-0.125", "-0.12"),
            ("2.675", "2.68"),
            ("2.6749", "2.67"),
            ("99.999999", "100.00"),
            ("5", "5.00"),
        ],
    )
    def test_rounds_half_even_to_cents(self, raw: str, expected: str) -> None:
        quantized = cad(raw).quantize()
        assert quantized == cad(expected)
        assert str(quantized.amount) == expected

    def test_is_quantized(self) -> None:
        assert cad("1.10").is_quantized
        assert cad("1.1").is_quantized
        assert cad("7").is_quantized
        assert not cad("1.105").is_quantized


class TestAllocate:
    def test_three_equal_parts_put_the_extra_cent_first(self) -> None:
        assert allocate(cad("100.00"), [Decimal(1)] * 3) == (
            cad("33.34"),
            cad("33.33"),
            cad("33.33"),
        )

    def test_largest_remainder_wins_the_leftover_cent(self) -> None:
        # Exact shares of 0.05 by 3:7 are 0.015 and 0.035. Both floors leave
        # 0.5 cent. The tie goes to the first part: 0.02 and 0.03.
        assert allocate(cad("0.05"), [Decimal(3), Decimal(7)]) == (cad("0.02"), cad("0.03"))
        # Exact shares of 10.00 by 1:2 are 3.333... and 6.666...; the second
        # part has the larger remainder and gets the leftover cent.
        assert allocate(cad("10.00"), [Decimal(1), Decimal(2)]) == (cad("3.33"), cad("6.67"))

    def test_negative_total_mirrors_the_positive_split(self) -> None:
        assert allocate(usd("-100.00"), [Decimal(1)] * 3) == (
            usd("-33.34"),
            usd("-33.33"),
            usd("-33.33"),
        )

    def test_zero_weight_gets_zero(self) -> None:
        assert allocate(cad("1.00"), [Decimal(0), Decimal(1)]) == (cad("0.00"), cad("1.00"))

    def test_fractional_weights(self) -> None:
        assert allocate(cad("1.00"), [Decimal("0.25"), Decimal("0.75")]) == (
            cad("0.25"),
            cad("0.75"),
        )

    def test_rejects_unquantized_total(self) -> None:
        with pytest.raises(ValueError, match="quantized"):
            allocate(cad("1.005"), [Decimal(1), Decimal(1)])

    @pytest.mark.parametrize(
        ("weights", "message"),
        [
            ([], "empty"),
            ([Decimal(-1), Decimal(2)], "negative"),
            ([Decimal(0), Decimal(0)], "positive"),
            ([Decimal("NaN")], "finite"),
        ],
    )
    def test_rejects_invalid_weights(self, weights: list[Decimal], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            allocate(cad("1.00"), weights)

    def test_rejects_float_weights(self) -> None:
        with pytest.raises(TypeError, match=r"decimal\.Decimal"):
            allocate(cad("1.00"), [0.5, 0.5])  # type: ignore[list-item]
