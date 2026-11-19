"""Validation and serialization rules of the canonical events."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import ClassVar

import pytest
from pydantic import TypeAdapter, ValidationError

from northstar.domain.events import (
    CashDeposited,
    CashDepositedPayload,
    Event,
    OrderPlacedPayload,
)
from northstar.infrastructure.event_files import serialize_event

from factories import deposit, execution

ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def _payload(**overrides: object) -> dict[str, object]:
    return {
        "cash_movement_id": "cm_1",
        "account_id": "acc_1",
        "amount": "10.00",
        "currency": "CAD",
    } | overrides


class TestAmounts:
    def test_float_is_rejected_not_converted(self) -> None:
        with pytest.raises(ValidationError, match="floats are not accepted"):
            CashDepositedPayload.model_validate(_payload(amount=0.1))

    @pytest.mark.parametrize(
        "amount", ["10.001", "0", "-5.00", "NaN", "Infinity", "1" * 17 + ".00"]
    )
    def test_invalid_amounts_are_rejected(self, amount: str) -> None:
        with pytest.raises(ValidationError):
            CashDepositedPayload.model_validate(_payload(amount=amount))

    def test_decimal_strings_are_parsed_exactly(self) -> None:
        assert CashDepositedPayload.model_validate(_payload(amount="0.10")).amount == Decimal(
            "0.10"
        )

    def test_json_carries_fixed_point_strings(self) -> None:
        payload = CashDepositedPayload.model_validate(_payload(amount="1E+2"))
        assert payload.model_dump_json() == (
            '{"cash_movement_id":"cm_1","account_id":"acc_1","amount":"100","currency":"CAD"}'
        )

    def test_unknown_currency_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CashDepositedPayload.model_validate(_payload(currency="EUR"))


class TestTimestamps:
    def _event(self, ts: datetime, recorded: datetime | None = None) -> CashDeposited:
        return CashDeposited(
            event_id="evt_1",
            event_timestamp=ts,
            recorded_at=recorded or ts,
            correlation_id="acc_1",
            payload=CashDepositedPayload.model_validate(_payload()),
        )

    def test_utc_is_accepted(self) -> None:
        self._event(datetime(2026, 3, 3, 15, 0, tzinfo=UTC))

    def test_naive_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._event(datetime(2026, 3, 3, 15, 0))  # noqa: DTZ001 - the case under test

    def test_non_utc_offset_is_rejected(self) -> None:
        toronto_winter = timezone(timedelta(hours=-5))
        with pytest.raises(ValidationError, match="UTC"):
            self._event(datetime(2026, 3, 3, 10, 0, tzinfo=toronto_winter))

    def test_recorded_before_the_event_is_rejected(self) -> None:
        ts = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="recorded_at"):
            self._event(ts, recorded=ts - timedelta(seconds=1))


class TestEnvelope:
    def test_json_round_trip_is_lossless(self) -> None:
        for event in (deposit(), execution()):
            assert ADAPTER.validate_json(serialize_event(event)) == event

    def test_unknown_event_type_is_rejected(self) -> None:
        raw = serialize_event(deposit()).replace('"cash_deposited"', '"cash_teleported"')
        with pytest.raises(ValidationError):
            ADAPTER.validate_json(raw)

    def test_unknown_schema_version_is_rejected(self) -> None:
        raw = serialize_event(deposit()).replace('"schema_version":1', '"schema_version":2')
        with pytest.raises(ValidationError):
            ADAPTER.validate_json(raw)

    def test_extra_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CashDepositedPayload.model_validate(_payload(note="extra"))

    def test_events_are_immutable(self) -> None:
        event = deposit()
        with pytest.raises(ValidationError):
            event.event_id = "evt_other"  # type: ignore[misc]


class TestOrders:
    BASE: ClassVar[dict[str, object]] = {
        "order_id": "ord_1",
        "account_id": "acc_1",
        "instrument_id": "ins_1",
        "market": "XTSE",
        "side": "BUY",
        "quantity": "10",
        "currency": "CAD",
    }

    def test_limit_order_needs_a_limit_price(self) -> None:
        with pytest.raises(ValidationError, match="limit_price"):
            OrderPlacedPayload.model_validate(self.BASE | {"order_type": "LIMIT"})
        OrderPlacedPayload.model_validate(
            self.BASE | {"order_type": "LIMIT", "limit_price": "52.40"}
        )

    def test_market_order_must_not_have_a_limit_price(self) -> None:
        with pytest.raises(ValidationError, match="limit_price"):
            OrderPlacedPayload.model_validate(
                self.BASE | {"order_type": "MARKET", "limit_price": "52.40"}
            )
