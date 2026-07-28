from datetime import date
from decimal import Decimal

from app.scenarios.procurement.contracts import (
    ProcurementItemDraft,
    ProcurementRequestDraft,
)


def test_procurement_draft_reports_only_explicit_missing_fields() -> None:
    incomplete = ProcurementRequestDraft(requester_id="EMP-1001")
    assert incomplete.missing_fields() == (
        "items",
        "estimated_total_amount",
        "cost_center_code",
        "desired_date",
        "delivery_location_code",
        "business_reason",
    )

    complete = ProcurementRequestDraft(
        requester_id="EMP-1001",
        items=(
            ProcurementItemDraft(
                item_name="Display",
                item_category="OFFICE_EQUIPMENT",
                quantity=1,
            ),
        ),
        estimated_total_amount=Decimal("2600.00"),
        cost_center_code="CC-SALES-EAST-001",
        desired_date=date(2026, 8, 5),
        delivery_location_code="SHANGHAI-HQ",
        business_reason="Project delivery support",
    )
    assert complete.missing_fields() == ()
