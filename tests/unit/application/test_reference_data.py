"""Calendar reference rows come from the domain calendar, one per covered date."""

from datetime import timedelta

from northstar.application.reference_data import calendar_days

from factories import policies


def test_one_row_per_covered_date_with_the_calendars_own_answers() -> None:
    calendars = policies().calendars
    rows = calendar_days(calendars)
    for market, calendar in calendars.items():
        mine = [r for r in rows if r.market == market]
        assert len(mine) == (calendar.coverage.end - calendar.coverage.start).days + 1
        holidays = {h.date: h.name for h in calendar.settlement_holidays}
        for r in mine:
            assert r.calendar_version == calendar.version
            assert r.is_trading_day == calendar.is_trading_day(r.calendar_date)
            assert r.is_settlement_day == calendar.is_settlement_day(r.calendar_date)
            assert r.settlement_holiday_name == holidays.get(r.calendar_date)
            if r.next_settlement_day is not None:
                gap = r.next_settlement_day - r.calendar_date
                assert gap >= timedelta(days=1)
                assert calendar.is_settlement_day(r.next_settlement_day)
                skipped = [r.calendar_date + timedelta(days=n) for n in range(1, gap.days)]
                assert not any(calendar.is_settlement_day(d) for d in skipped)
        # Neighbours that would leave the coverage are null, not guessed.
        assert mine[0].previous_settlement_day is None
        assert mine[-1].next_settlement_day is None


def test_remembrance_day_trades_but_does_not_settle_in_cad() -> None:
    (day,) = [
        r
        for r in calendar_days(policies().calendars)
        if r.market == "XTSE" and r.calendar_date.isoformat() == "2026-11-11"
    ]
    assert (day.is_trading_day, day.is_settlement_day) == (True, False)
    assert day.trading_holiday_name is None
    assert day.settlement_holiday_name is not None
