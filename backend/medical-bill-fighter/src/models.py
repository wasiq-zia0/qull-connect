"""Pydantic models for the medical-bill-fighter connector.

PII MINIMIZATION: we collect only what the intake needs — name/email for
letters and billing contact, provider name, dates, line items, EOB figures.
We do NOT collect diagnoses, procedure notes, insurance member IDs, or any
other clinical data. All free-text fields are treated as untrusted data and
are escaped before rendering into PDFs or tool output.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import ConfigDict, BaseModel, EmailStr, Field, field_validator


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class LineItem(SafeModel):
    code: str = Field(min_length=1, max_length=20, description="CPT/HCPCS code")
    description: str = Field(min_length=1, max_length=300)
    amount: float = Field(ge=0, le=1_000_000)
    quantity: int = Field(default=1, ge=1, le=1000)

    @field_validator("code")
    @classmethod
    def _norm_code(cls, v: str) -> str:
        return v.strip().upper()


class EobInfo(SafeModel):
    allowed_amount: float = Field(ge=0, le=1_000_000)
    patient_responsibility: float = Field(ge=0, le=1_000_000)
    deductible_applied: float = Field(default=0.0, ge=0, le=1_000_000)
    coinsurance: float = Field(default=0.0, ge=0, le=1_000_000)


class CaseIntake(SafeModel):
    patient_name: str = Field(min_length=1, max_length=120)
    patient_email: Optional[EmailStr] = None
    provider_name: str = Field(min_length=1, max_length=200)
    bill_date: date
    # Billed total the patient was asked to pay (used for bill-vs-EOB comparison).
    billed_patient_responsibility: float = Field(ge=0, le=1_000_000)
    line_items: list[LineItem] = Field(min_length=1, max_length=200)
    eob: Optional[EobInfo] = None
    insurance_plan: Optional[str] = Field(default=None, max_length=200)
    is_emergency: bool = False
    is_out_of_network: bool = False
    facility_in_network: bool = True


class OutcomeReport(SafeModel):
    reduction_amount: float = Field(ge=0, le=1_000_000,
                                   description="Dollar amount the user confirms their bill was reduced by")


PackType = Literal["dispute", "itemized", "assistance", "negotiate"]

PACK_DESCRIPTIONS: dict[str, str] = {
    "dispute": "Billing-error dispute letter populated with the detected findings.",
    "itemized": "Request for a fully itemized bill with CPT/HCPCS detail.",
    "assistance": "Financial-assistance / charity-care request letter.",
    "negotiate": "Prompt-pay discount negotiation script.",
}

LEGAL_NOTICE = "Template automation — NOT legal advice. Review before sending."
