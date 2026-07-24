from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, model_validator


class RentIncrease(BaseModel):
    effective_on: date
    new_amount: Decimal
    notice_given_on: date | None = None


class LeaseInput(BaseModel):
    rent_amount: Decimal
    rent_frequency: Literal["weekly", "fortnightly", "monthly"]
    start_date: date
    end_date: date | None = None
    bond_amount: Decimal | None = None
    rent_in_advance_amount: Decimal | None = None
    holding_deposit_amount: Decimal | None = None
    other_security_amount: Decimal | None = None
    break_fee_amount: Decimal | None = None
    rent_increases: list[RentIncrease] | None = None
    fixed_term_increase_in_agreement: bool | None = None

    @model_validator(mode="after")
    def end_after_start(self) -> "LeaseInput":
        if self.end_date is not None and self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self
