from __future__ import annotations

from decimal import Decimal

import pytest
from app.errors import ApiError
from app.logistics import (
    CONSIGNMENT_TRANSITIONS,
    TRIP_TRANSITIONS,
    check_capacity,
    classify_receipt,
    refuse_transition,
    roll_up_request_status,
)


class TestTransitionTables:
    def test_terminal_states_allow_nothing(self) -> None:
        """A delivered consignment is corrected by a new receipt, not by
        rewinding its status; the same holds for every other end state."""

        for status in ("delivered", "partially_delivered", "failed", "cancelled"):
            assert CONSIGNMENT_TRANSITIONS[status] == frozenset()
        for status in ("completed", "failed", "cancelled"):
            assert TRIP_TRANSITIONS[status] == frozenset()

    def test_a_trip_cannot_skip_straight_to_completed(self) -> None:
        assert "completed" not in TRIP_TRANSITIONS["planned"]
        assert "completed" not in TRIP_TRANSITIONS["awaiting_driver"]
        assert "completed" in TRIP_TRANSITIONS["active"]

    def test_a_consignment_cannot_be_delivered_before_it_moves(self) -> None:
        assert "delivered" not in CONSIGNMENT_TRANSITIONS["draft"]
        assert "delivered" not in CONSIGNMENT_TRANSITIONS["assigned"]
        assert "delivered" in CONSIGNMENT_TRANSITIONS["in_transit"]

    def test_every_reachable_state_has_an_entry(self) -> None:
        """A state that can be reached but has no row would raise KeyError at
        runtime instead of refusing the move."""

        for table in (CONSIGNMENT_TRANSITIONS, TRIP_TRANSITIONS):
            reachable = {target for targets in table.values() for target in targets}
            assert reachable <= set(table)

    def test_refusal_names_both_states(self) -> None:
        error = refuse_transition("trip", "planned", "completed")
        assert error.status_code == 409
        assert error.code == "invalid_transition"
        assert "planned" in error.message and "completed" in error.message


class TestCapacityCheck:
    def test_an_unweighed_load_is_reported_as_unchecked_not_as_fitting(self) -> None:
        """Treating a missing weight as zero would pass an overloaded vehicle
        through a check that never happened."""

        result = check_capacity(
            consignment_weight_kg=None, vehicle_capacity_kg=Decimal("8000")
        )
        assert result.performed is False
        assert result.within_capacity is None
        assert "no declared weight" in (result.reason or "")

    def test_a_vehicle_without_declared_capacity_is_also_unchecked(self) -> None:
        result = check_capacity(
            consignment_weight_kg=Decimal("500"), vehicle_capacity_kg=None
        )
        assert result.performed is False
        assert result.within_capacity is None

    def test_an_overweight_load_is_identified(self) -> None:
        result = check_capacity(
            consignment_weight_kg=Decimal("9000"), vehicle_capacity_kg=Decimal("8000")
        )
        assert result.performed is True
        assert result.within_capacity is False

    def test_a_load_exactly_at_capacity_fits(self) -> None:
        result = check_capacity(
            consignment_weight_kg=Decimal("8000"), vehicle_capacity_kg=Decimal("8000")
        )
        assert result.within_capacity is True


class TestReceiptClassification:
    ORDERED = {"a": Decimal("10"), "b": Decimal("5")}

    def test_everything_arriving_is_delivered(self) -> None:
        assert (
            classify_receipt(
                ordered=self.ORDERED,
                delivered={"a": Decimal("10"), "b": Decimal("5")},
            )
            == "delivered"
        )

    def test_a_short_line_makes_the_whole_receipt_partial(self) -> None:
        assert (
            classify_receipt(
                ordered=self.ORDERED,
                delivered={"a": Decimal("10"), "b": Decimal("4")},
            )
            == "partially_delivered"
        )

    def test_an_omitted_line_counts_as_nothing_delivered(self) -> None:
        """A line the receipt does not mention did not arrive. Reading silence
        as 'fully delivered' would quietly overstate what reached the facility."""

        assert (
            classify_receipt(ordered=self.ORDERED, delivered={"a": Decimal("10")})
            == "partially_delivered"
        )

    def test_nothing_arriving_is_a_failure_not_a_partial(self) -> None:
        assert (
            classify_receipt(
                ordered=self.ORDERED, delivered={"a": Decimal("0"), "b": Decimal("0")}
            )
            == "failed"
        )
        assert classify_receipt(ordered=self.ORDERED, delivered={}) == "failed"

    def test_over_delivery_still_counts_as_delivered(self) -> None:
        """The API refuses over-delivery before it reaches this function; if it
        ever arrives here it must not be misread as partial."""

        assert (
            classify_receipt(
                ordered=self.ORDERED,
                delivered={"a": Decimal("11"), "b": Decimal("5")},
            )
            == "delivered"
        )

    def test_a_consignment_with_no_items_is_refused(self) -> None:
        with pytest.raises(ApiError) as excinfo:
            classify_receipt(ordered={}, delivered={})
        assert excinfo.value.status_code == 422
        assert excinfo.value.code == "consignment_has_no_items"


class TestSupplyRequestRollup:
    """A request is a facility's need, not a shipment."""

    def test_a_request_with_no_consignments_is_still_open(self) -> None:
        assert roll_up_request_status(consignment_statuses=[], current="open") == "open"

    def test_planning_alone_does_not_fulfil_anything(self) -> None:
        assert (
            roll_up_request_status(
                consignment_statuses=["planned", "assigned", "in_transit"],
                current="open",
            )
            == "planned"
        )

    def test_every_consignment_delivered_fulfils_the_request(self) -> None:
        assert (
            roll_up_request_status(
                consignment_statuses=["delivered", "delivered"], current="planned"
            )
            == "fulfilled"
        )

    def test_one_short_consignment_keeps_the_request_partial(self) -> None:
        assert (
            roll_up_request_status(
                consignment_statuses=["delivered", "partially_delivered"],
                current="planned",
            )
            == "partially_fulfilled"
        )

    def test_a_delivered_consignment_alongside_one_still_moving_is_partial(
        self,
    ) -> None:
        """The facility has some of what it asked for, not all of it."""

        assert (
            roll_up_request_status(
                consignment_statuses=["delivered", "in_transit"], current="planned"
            )
            == "partially_fulfilled"
        )

    def test_cancelled_consignments_are_ignored(self) -> None:
        assert (
            roll_up_request_status(
                consignment_statuses=["delivered", "cancelled"], current="planned"
            )
            == "fulfilled"
        )
        assert (
            roll_up_request_status(
                consignment_statuses=["cancelled", "cancelled"], current="planned"
            )
            == "open"
        )

    def test_a_cancelled_request_stays_cancelled(self) -> None:
        assert (
            roll_up_request_status(
                consignment_statuses=["delivered"], current="cancelled"
            )
            == "cancelled"
        )

    def test_a_failed_consignment_does_not_fulfil_the_request(self) -> None:
        assert (
            roll_up_request_status(consignment_statuses=["failed"], current="planned")
            == "planned"
        )


def test_a_receipt_may_name_each_item_once() -> None:
    """Two lines for one item would keep only the last quantity, silently."""

    from app.logistics import ReceiptCreateRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="more than one line"):
        ReceiptCreateRequest(
            status="delivered",
            items=[
                {"consignment_item_id": "a", "delivered_quantity": "10"},
                {"consignment_item_id": "a", "delivered_quantity": "2"},
            ],
        )
    ReceiptCreateRequest(
        status="delivered",
        items=[
            {"consignment_item_id": "a", "delivered_quantity": "10"},
            {"consignment_item_id": "b", "delivered_quantity": "5"},
        ],
    )
