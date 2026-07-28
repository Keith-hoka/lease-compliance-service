# LLM Clause Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Async clause-audit jobs that judge a lease document (PDF or text) against NSW law with an LLM, returning cited findings that may abstain (`yellow`), with per-rule P/R evals.

**Architecture:** A `clause_audit_jobs` table holds the document transiently; a lifespan asyncio worker claims jobs with `FOR UPDATE SKIP LOCKED`, renders the document (text-first, native PDF fallback), makes one cache-sharing Claude call per check family with corpus-injected statutory text, verifies quotes and resolves citations in code, then wipes the document in the finalising commit. Evals are two-layer: mocked-LLM tests in CI, `llm_eval`-marked golden sets against the real model.

**Tech Stack:** FastAPI + async SQLAlchemy 2.0 + Alembic + PostgreSQL, `anthropic` SDK (`messages.parse` + Pydantic), `pypdf`, `python-multipart`; dev: `fpdf2` + `pillow` for PDF fixtures.

**Spec:** `docs/superpowers/specs/2026-07-28-llm-clause-audit-design.md`

## Global Constraints

- Python 3.12+, `uv` only (`uv run`, `uv add`); no emojis anywhere.
- Every task ends: full suite (`uv run pytest`) -> ruff sequence (`uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`) -> commit -> push -> CI green.
- Model default `claude-opus-4-8` via `settings.clause_audit_model`; API key via `settings.anthropic_api_key` (empty = feature disabled, POST returns 503).
- Limits (exact): file <= 10 MB else 413; text <= 200_000 chars else 413; PDF text layer >= 200 chars/page average -> text path; `max_tokens=8000`; job timeout 900 s; idle poll 2 s.
- Document wiped (`document = NULL`) in the same commit that finalises the job — success AND failure AND startup sweep.
- `findings` always cite (existing `Citation` shape); `discrepancies` never cite. The deterministic engine never emits `yellow`.
- Eval thresholds v1: per-rule precision >= 0.9, recall >= 0.8; `yellow` on a red case counts as a recall miss, never a precision hit.
- The LLM judges only statutory text injected from the corpus via `section_at`; the model never produces citations (rule -> `SectionRef` pinned in code).
- System prompt carries the general-information-not-legal-advice framing.
- Statutory pinning discipline: before finalising any rule list, dump the sections from the corpus and paste the governing text into the code's docstrings/questions (Tasks 4 and 8 include the exact commands).

---

### Task 1: Yellow verdict, clause-audit schemas, job model, migration

**Files:**
- Modify: `app/rules/base.py:30` (verdict Literal)
- Create: `app/schemas/clause_audit.py`
- Create: `app/models/clause_audit.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/a1c47e92b5d3_clause_audit_jobs.py`
- Test: `tests/test_clause_schemas.py`, `tests/test_models.py` (append)

**Interfaces:**
- Consumes: `Finding`, `Citation` from `app.rules.base`; `Base` from `app.core.db`.
- Produces: `ClauseFinding(Finding)` with `clause_quote: str | None`; `Discrepancy(field, document_value, submitted_value)` (all `str`); `ClauseLeaseInput` (9 optional money/date fields); `ClauseAuditCreate(jurisdiction, as_at, client_ref, lease)`; `ClauseAuditInfo`; ORM `ClauseAuditJob` (tablename `clause_audit_jobs`). Later tasks import these names verbatim.

- [ ] **Step 1: Write the failing tests**

`tests/test_clause_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from app.rules.base import Finding
from app.schemas.clause_audit import ClauseAuditCreate, ClauseFinding, ClauseLeaseInput


def test_finding_accepts_yellow():
    f = Finding(rule_id="nsw.clause.carpet_cleaning", verdict="yellow", summary="unsure")
    assert f.verdict == "yellow"


def test_clause_finding_carries_quote():
    f = ClauseFinding(
        rule_id="nsw.clause.carpet_cleaning",
        verdict="red",
        summary="found",
        clause_quote="carpet professionally cleaned",
    )
    assert f.clause_quote == "carpet professionally cleaned"
    assert f.model_dump(mode="json")["clause_quote"]


def test_clause_lease_input_all_optional():
    assert ClauseLeaseInput().rent_amount is None


def test_create_payload_defaults():
    body = ClauseAuditCreate.model_validate({"jurisdiction": "NSW"})
    assert body.as_at is None and body.lease is None


def test_create_rejects_other_jurisdiction():
    with pytest.raises(ValidationError):
        ClauseAuditCreate.model_validate({"jurisdiction": "VIC"})
```

Append to `tests/test_models.py`:

```python
async def test_clause_audit_job_roundtrip(db_session):
    from app.models import ClauseAuditJob

    job = ClauseAuditJob(
        client_id="testco",
        jurisdiction="NSW",
        as_at=date(2026, 7, 28),
        document=b"lease text",
        document_kind="text",
        engine_version="1.1.0",
        model="claude-opus-4-8",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    assert job.status == "pending"
    assert job.findings == [] and job.discrepancies == []
    assert job.document == b"lease text" and job.completed_at is None
```

(In `tests/test_models.py`, ensure `from datetime import date` is among the
module imports — add it if absent; `ClauseAuditJob` is imported inside the
test body, so no other import changes are needed.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_clause_schemas.py tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: app.schemas.clause_audit`, then `ImportError: ClauseAuditJob`.

- [ ] **Step 3: Implement**

`app/rules/base.py` — change line 30 only:

```python
    verdict: Literal["red", "green", "yellow", "skipped"]
```

`app/schemas/clause_audit.py`:

```python
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.rules.base import Finding


class ClauseFinding(Finding):
    """A cited LLM finding; clause_quote is the lease text it rests on."""

    clause_quote: str | None = None


class Discrepancy(BaseModel):
    """A field cross-check mismatch. Data integrity, not law: no citation."""

    field: str
    document_value: str
    submitted_value: str


class ClauseLeaseInput(BaseModel):
    """Money/date subset of LeaseInput; all optional, presence gates family 2."""

    rent_amount: Decimal | None = None
    rent_frequency: Literal["weekly", "fortnightly", "monthly"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    bond_amount: Decimal | None = None
    rent_in_advance_amount: Decimal | None = None
    holding_deposit_amount: Decimal | None = None
    other_security_amount: Decimal | None = None
    break_fee_amount: Decimal | None = None


class ClauseAuditCreate(BaseModel):
    """The JSON `payload` part of the multipart POST."""

    jurisdiction: Literal["NSW"]
    as_at: date | None = None
    client_ref: str | None = None
    lease: ClauseLeaseInput | None = None


class ClauseAuditInfo(BaseModel):
    id: uuid.UUID
    status: Literal["pending", "running", "succeeded", "failed"]
    jurisdiction: str
    as_at: date
    engine_version: str
    model: str
    client_ref: str | None = None
    findings: list[ClauseFinding] = []
    discrepancies: list[Discrepancy] = []
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
```

`app/models/clause_audit.py`:

```python
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, JSON, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ClauseAuditJob(Base):
    __tablename__ = "clause_audit_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(50), index=True)
    client_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    jurisdiction: Mapped[str] = mapped_column(String(3))
    as_at: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")
    document: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    document_kind: Mapped[str] = mapped_column(String(4))
    lease: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    findings: Mapped[list] = mapped_column(JSON, default=list)
    discrepancies: Mapped[list] = mapped_column(JSON, default=list)
    engine_version: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(50))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`app/models/__init__.py`:

```python
from app.models.audit import Audit, AuditChange
from app.models.clause_audit import ClauseAuditJob
from app.models.legislation import Act, IngestedVersion, Section

__all__ = ["Act", "Audit", "AuditChange", "ClauseAuditJob", "IngestedVersion", "Section"]
```

`alembic/versions/a1c47e92b5d3_clause_audit_jobs.py`:

```python
"""clause_audit_jobs

Revision ID: a1c47e92b5d3
Revises: df9c4c593e57
Create Date: 2026-07-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c47e92b5d3"
down_revision: str | Sequence[str] | None = "df9c4c593e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the clause_audit_jobs table."""
    op.create_table(
        "clause_audit_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.String(50), nullable=False),
        sa.Column("client_ref", sa.String(100), nullable=True),
        sa.Column("jurisdiction", sa.String(3), nullable=False),
        sa.Column("as_at", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("document", sa.LargeBinary(), nullable=True),
        sa.Column("document_kind", sa.String(4), nullable=False),
        sa.Column("lease", sa.JSON(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("discrepancies", sa.JSON(), nullable=False),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_clause_audit_jobs_client_id", "clause_audit_jobs", ["client_id"])
    op.create_index("ix_clause_audit_jobs_client_ref", "clause_audit_jobs", ["client_ref"])


def downgrade() -> None:
    """Drop the clause_audit_jobs table."""
    op.drop_index("ix_clause_audit_jobs_client_ref", "clause_audit_jobs")
    op.drop_index("ix_clause_audit_jobs_client_id", "clause_audit_jobs")
    op.drop_table("clause_audit_jobs")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_clause_schemas.py tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Apply the migration to the dev store**

Run: `uv run alembic upgrade head`
Expected: `Running upgrade df9c4c593e57 -> a1c47e92b5d3`.

- [ ] **Step 6: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add the yellow verdict and clause audit job model" && git push origin main
```

Watch CI to green (`gh run watch`).

---

### Task 2: Document rendering (text-first, PDF fallback)

**Files:**
- Create: `app/clause_audit/__init__.py` (empty), `app/clause_audit/document.py`
- Create: `tests/fixtures/pdfs.py`
- Test: `tests/test_clause_document.py`

**Interfaces:**
- Produces: `DocumentInput(kind: Literal["text","pdf"], text: str | None, pdf: bytes | None)`; `document_input(kind: str, raw: bytes) -> DocumentInput`; `CHARS_PER_PAGE_MIN = 200`. Fixture helpers `make_text_pdf(text) -> bytes`, `make_scanned_pdf(text) -> bytes`.

- [ ] **Step 1: Add dependencies**

```bash
uv add pypdf
uv add --dev "fpdf2>=2.8" "pillow>=10.1"
```

- [ ] **Step 2: Write the fixture helpers**

`tests/fixtures/pdfs.py`:

```python
"""Generated PDF fixtures: a text-layer lease and a scanned (image-only) twin."""

import io
import textwrap

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont


def make_text_pdf(text: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(w=180, text=text)
    return bytes(pdf.output())


def make_scanned_pdf(text: str) -> bytes:
    """The same content rasterised: a page image with no text layer."""
    image = Image.new("RGB", (1200, 1600), "white")
    font = ImageFont.load_default(size=32)
    wrapped = textwrap.fill(text, width=60)
    ImageDraw.Draw(image).multiline_text((60, 60), wrapped, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    pdf = FPDF(unit="pt", format=(600, 800))
    pdf.add_page()
    pdf.image(buffer, x=0, y=0, w=600)
    return bytes(pdf.output())
```

- [ ] **Step 3: Write the failing tests**

`tests/test_clause_document.py`:

```python
from app.clause_audit.document import CHARS_PER_PAGE_MIN, document_input
from tests.fixtures.pdfs import make_scanned_pdf, make_text_pdf

LEASE = (
    "RESIDENTIAL TENANCY AGREEMENT. The weekly rent is $560 payable weekly. "
    "The tenant must have the carpet professionally cleaned at the end of the tenancy. "
) * 10


def test_plain_text_takes_text_path():
    doc = document_input("text", "rent is $560".encode())
    assert doc.kind == "text" and doc.text == "rent is $560" and doc.pdf is None


def test_text_layer_pdf_takes_text_path():
    doc = document_input("pdf", make_text_pdf(LEASE))
    assert doc.kind == "text"
    assert "professionally cleaned" in doc.text


def test_scanned_pdf_falls_back_to_pdf_path():
    raw = make_scanned_pdf(LEASE)
    doc = document_input("pdf", raw)
    assert doc.kind == "pdf" and doc.pdf == raw and doc.text is None


def test_threshold_constant():
    assert CHARS_PER_PAGE_MIN == 200
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_clause_document.py -v`
Expected: FAIL with `ModuleNotFoundError: app.clause_audit.document`.

- [ ] **Step 5: Implement**

`app/clause_audit/document.py`:

```python
"""Render a stored document for the LLM: text when a usable layer exists."""

import io
from dataclasses import dataclass
from typing import Literal

from pypdf import PdfReader

CHARS_PER_PAGE_MIN = 200


@dataclass(frozen=True)
class DocumentInput:
    kind: Literal["text", "pdf"]
    text: str | None = None
    pdf: bytes | None = None


def extract_pdf_text(data: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages), len(pages)


def document_input(kind: str, raw: bytes) -> DocumentInput:
    if kind == "text":
        return DocumentInput(kind="text", text=raw.decode("utf-8"))
    text, page_count = extract_pdf_text(raw)
    if page_count and len(text) / page_count >= CHARS_PER_PAGE_MIN:
        return DocumentInput(kind="text", text=text)
    return DocumentInput(kind="pdf", pdf=raw)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_clause_document.py -v`
Expected: PASS.

- [ ] **Step 7: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add document rendering with text-first PDF fallback" && git push origin main
```

---

### Task 3: LLM plumbing — prompts, output models, request builder, judge

**Files:**
- Create: `app/llm/__init__.py` (empty), `app/llm/schemas.py`, `app/llm/prompts.py`, `app/llm/client.py`
- Modify: `app/core/config.py`
- Test: `tests/test_llm_plumbing.py`

**Interfaces:**
- Consumes: `DocumentInput` from Task 2.
- Produces: `family_output_model(name, rule_ids) -> type[BaseModel]` (items with enum-locked `rule_id`, `verdict: Literal["red","green","yellow"]`, `reasoning: str`, `clause_quote: str | None`); `FieldsOutput(fields: list[FieldExtraction])` with `FieldExtraction(field, document_value, quote)`; `SYSTEM: str`; `clause_instruction(family_name, as_at, sections, rules) -> str` where `sections: dict[tuple[str, str], str]` and `rules: list[tuple[str, str]]` (rule_id, question); `fields_instruction() -> str`; `document_block(doc) -> dict`; `build_parse_kwargs(model, doc, instruction) -> dict`; `JudgeFn = Callable[[DocumentInput, str, type[BaseModel]], Awaitable[BaseModel]]`; `make_judge() -> JudgeFn`; `JudgeError`. Settings gain `anthropic_api_key: str = ""`, `clause_audit_model: str = "claude-opus-4-8"`, and `clause_audit_enabled() -> bool`.

- [ ] **Step 1: Add the SDK**

```bash
uv add anthropic
```

- [ ] **Step 2: Write the failing tests**

`tests/test_llm_plumbing.py`:

```python
from datetime import date

import pytest
from pydantic import ValidationError

from app.clause_audit.document import DocumentInput
from app.llm.client import build_parse_kwargs, document_block
from app.llm.prompts import SYSTEM, clause_instruction, fields_instruction
from app.llm.schemas import FieldsOutput, family_output_model

IDS = ["nsw.clause.carpet_cleaning", "nsw.clause.fumigation"]


def test_family_output_model_locks_rule_ids():
    model = family_output_model("ProhibitedOutput", IDS)
    parsed = model.model_validate(
        {
            "items": [
                {
                    "rule_id": "nsw.clause.carpet_cleaning",
                    "verdict": "red",
                    "reasoning": "found",
                    "clause_quote": "carpet professionally cleaned",
                }
            ]
        }
    )
    assert parsed.items[0].rule_id == "nsw.clause.carpet_cleaning"
    with pytest.raises(ValidationError):
        model.model_validate(
            {"items": [{"rule_id": "nsw.invented", "verdict": "red", "reasoning": "x"}]}
        )


def test_fields_output_locks_field_names():
    parsed = FieldsOutput.model_validate(
        {"fields": [{"field": "rent_amount", "document_value": "$560 per week", "quote": "x"}]}
    )
    assert parsed.fields[0].document_value == "$560 per week"
    with pytest.raises(ValidationError):
        FieldsOutput.model_validate(
            {"fields": [{"field": "made_up", "document_value": "1", "quote": None}]}
        )


def test_text_document_block_carries_cache_control():
    block = document_block(DocumentInput(kind="text", text="lease body"))
    assert block == {
        "type": "text",
        "text": "lease body",
        "cache_control": {"type": "ephemeral"},
    }


def test_pdf_document_block_is_base64_document():
    block = document_block(DocumentInput(kind="pdf", pdf=b"%PDF-fake"))
    assert block["type"] == "document"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "application/pdf"
    assert block["cache_control"] == {"type": "ephemeral"}


def test_build_parse_kwargs_shape():
    doc = DocumentInput(kind="text", text="lease body")
    kwargs = build_parse_kwargs("claude-opus-4-8", doc, "judge these rules")
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["max_tokens"] == 8000
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["system"] == SYSTEM
    content = kwargs["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1] == {"type": "text", "text": "judge these rules"}


def test_clause_instruction_embeds_statute_and_rules():
    text = clause_instruction(
        "prohibited terms",
        date(2026, 7, 28),
        {("act-2010-042", "19"): "Prohibited terms\n(2) Terms having the following effects..."},
        [("nsw.clause.carpet_cleaning", "A term requiring professional carpet cleaning.")],
    )
    assert "2026-07-28" in text
    assert "Prohibited terms" in text
    assert "nsw.clause.carpet_cleaning" in text
    assert "act-2010-042 s 19" in text


def test_fields_instruction_lists_every_field():
    text = fields_instruction()
    for name in ("rent_amount", "break_fee_amount", "start_date"):
        assert name in text


def test_system_prompt_disclaims():
    assert "general information" in SYSTEM
    assert "not legal advice" in SYSTEM
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_plumbing.py -v`
Expected: FAIL with `ModuleNotFoundError: app.llm.client`.

- [ ] **Step 4: Implement**

`app/core/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration, overridable via environment or .env."""

    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://rental:rental@localhost:5433/lease_compliance"
    api_keys: str = ""
    anthropic_api_key: str = ""
    clause_audit_model: str = "claude-opus-4-8"


settings = Settings()


def clause_audit_enabled() -> bool:
    return bool(settings.anthropic_api_key)
```

`app/llm/schemas.py`:

```python
"""Structured-output models. Rule ids are enum-locked so the model cannot invent rules."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, create_model

FIELD_NAMES = (
    "rent_amount",
    "rent_frequency",
    "start_date",
    "end_date",
    "bond_amount",
    "rent_in_advance_amount",
    "holding_deposit_amount",
    "other_security_amount",
    "break_fee_amount",
)


def family_output_model(name: str, rule_ids: list[str]) -> type[BaseModel]:
    rule_enum = StrEnum(f"{name}RuleId", {rid.replace(".", "_"): rid for rid in rule_ids})
    item = create_model(
        f"{name}Item",
        rule_id=(rule_enum, ...),
        verdict=(Literal["red", "green", "yellow"], ...),
        reasoning=(str, ...),
        clause_quote=(str | None, None),
    )
    return create_model(name, items=(list[item], ...))


class FieldExtraction(BaseModel):
    field: Literal[
        "rent_amount",
        "rent_frequency",
        "start_date",
        "end_date",
        "bond_amount",
        "rent_in_advance_amount",
        "holding_deposit_amount",
        "other_security_amount",
        "break_fee_amount",
    ]
    document_value: str | None
    quote: str | None = None


class FieldsOutput(BaseModel):
    fields: list[FieldExtraction]
```

`app/llm/prompts.py`:

```python
"""Prompt text. SYSTEM is byte-identical across families so the cache prefix holds."""

from datetime import date

from app.llm.schemas import FIELD_NAMES

SYSTEM = (
    "You are a compliance checker for New South Wales residential tenancy documents. "
    "You judge lease clauses strictly against the statutory text supplied in the "
    "instruction; never rely on remembered law and never cite anything not supplied. "
    "If the document or a clause is ambiguous, unreadable, or only partially matches, "
    "answer yellow rather than guessing. When you report a violation, quote the "
    "offending clause verbatim from the document. Return one item for every rule you "
    "are asked about. Your output is general information, not legal advice."
)


def clause_instruction(
    family_name: str,
    as_at: date,
    sections: dict[tuple[str, str], str],
    rules: list[tuple[str, str]],
) -> str:
    parts = [f"Check family: {family_name}. Statutory text in force at {as_at.isoformat()}:"]
    for (slug, section_no), text in sections.items():
        parts.append(f"--- {slug} s {section_no} ---\n{text}")
    parts.append(
        "Judge the document against each rule below. Return exactly one item per "
        "rule_id. verdict red means the rule is breached, green means it is not, "
        "yellow means you cannot tell. For red verdicts, clause_quote must be the "
        "verbatim offending text from the document."
    )
    for rule_id, question in rules:
        parts.append(f"- {rule_id}: {question}")
    return "\n\n".join(parts)


def fields_instruction() -> str:
    names = ", ".join(FIELD_NAMES)
    return (
        "Extract the following lease fields from the document, exactly as written: "
        f"{names}. Return one item per field. document_value is the verbatim value "
        "from the document, or null if the document does not state it. quote is the "
        "sentence or table cell it came from. Do not convert units or normalise; "
        "copy what the document says."
    )
```

`app/llm/client.py`:

```python
"""The judge: one structured-output call per check family, cache-sharing request shape."""

import base64
from collections.abc import Awaitable, Callable

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.clause_audit.document import DocumentInput
from app.core.config import settings
from app.llm.prompts import SYSTEM

JudgeFn = Callable[[DocumentInput, str, type[BaseModel]], Awaitable[BaseModel]]


class JudgeError(RuntimeError):
    pass


def document_block(doc: DocumentInput) -> dict:
    if doc.kind == "text":
        return {"type": "text", "text": doc.text, "cache_control": {"type": "ephemeral"}}
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(doc.pdf).decode(),
        },
        "cache_control": {"type": "ephemeral"},
    }


def build_parse_kwargs(model: str, doc: DocumentInput, instruction: str) -> dict:
    return {
        "model": model,
        "max_tokens": 8000,
        "thinking": {"type": "adaptive"},
        "system": SYSTEM,
        "messages": [
            {
                "role": "user",
                "content": [document_block(doc), {"type": "text", "text": instruction}],
            }
        ],
    }


def make_judge() -> JudgeFn:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def judge(doc: DocumentInput, instruction: str, output_model: type[BaseModel]):
        kwargs = build_parse_kwargs(settings.clause_audit_model, doc, instruction)
        response = await client.messages.parse(**kwargs, output_format=output_model)
        if response.stop_reason == "refusal":
            raise JudgeError("model declined the request")
        if response.parsed_output is None:
            raise JudgeError("model returned no parseable output")
        return response.parsed_output

    return judge
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm_plumbing.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add LLM prompts, output models and the judge" && git push origin main
```

---

### Task 4: Clause rules — pin from the corpus, define both rule lists

**Files:**
- Create: `app/clause_audit/rules.py`
- Test: `tests/test_clause_rules.py`

**Interfaces:**
- Consumes: `SectionRef`, `Citation` from `app.rules.base`; `section_at`; `COMMENCED` from `app.rules.nsw`.
- Produces: `ClauseRule(rule_id, family, ref: SectionRef, applies_from, applies_to, question)`; `PROHIBITED_RULES: list[ClauseRule]`; `MANDATORY_RULES: list[ClauseRule]`; `rule_active(rule, as_at) -> bool`; `resolve_rule(session, rule, as_at) -> Citation | None`; `statutory_texts(session, rules, as_at) -> dict[tuple[str, str], str]`.

- [ ] **Step 1: Pin the statutory text (do this first, against the dev store)**

Dump Act s 19 as in force today:

```bash
uv run python - <<'EOF'
import asyncio
from datetime import date
from app.core.db import async_session_factory
from app.services.legislation import section_at

async def main():
    async with async_session_factory() as session:
        sec = await section_at(session, "act-2010-042", "19", date(2026, 7, 28))
        print(sec.heading)
        print(sec.body_text)

asyncio.run(main())
EOF
```

Scan the Regulation for prescribed prohibited terms:

```bash
uv run python - <<'EOF'
import asyncio
from sqlalchemy import select
from app.core.db import async_session_factory
from app.models import Act, Section

async def main():
    async with async_session_factory() as session:
        query = (
            select(Section.section_no, Section.heading)
            .join(Act, Act.id == Section.act_id)
            .where(
                Act.slug == "sl-2019-0629",
                Section.valid_to.is_(None),
                Section.body_text.ilike("%prohibit%")
                | Section.body_text.ilike("%must not be included%"),
            )
        )
        for no, heading in (await session.execute(query)).all():
            print(no, heading)

asyncio.run(main())
EOF
```

Dump Act ss 12-26 (agreement content requirements) the same way (loop `section_no` over `[str(n) for n in range(12, 27)]` with the first snippet) and note every "must" imposed on what the agreement contains.

**Deliverable of this step:** the final `PROHIBITED_RULES` list (one rule per s 19(2) paragraph plus one per Regulation-prescribed term, with the paragraph's commencement as `applies_from` — s 19(2)(a)-(b) items existed at commencement, so they use `COMMENCED`; anything added by a later reform uses the corpus window's `valid_from` for the paragraph that introduced it) and the `MANDATORY_RULES` list (5-8 crisply decidable content requirements; exclude vague candidates and note them in `docs/rule-candidates.md`). Every rule's `question` paraphrases the pinned text and names the operative words.

- [ ] **Step 2: Write the failing tests**

`tests/test_clause_rules.py`:

```python
from datetime import date

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clause_audit.rules import (
    MANDATORY_RULES,
    PROHIBITED_RULES,
    ClauseRule,
    resolve_rule,
    rule_active,
    statutory_texts,
)
from app.models import Act
from app.rules.base import SectionRef

AS_AT = date(2026, 7, 28)


def test_rule_lists_are_populated_and_distinct():
    ids = [r.rule_id for r in PROHIBITED_RULES + MANDATORY_RULES]
    assert len(ids) == len(set(ids))
    assert any(r.rule_id == "nsw.clause.carpet_cleaning" for r in PROHIBITED_RULES)
    assert all(r.family == "prohibited" for r in PROHIBITED_RULES)
    assert all(r.family == "mandatory" for r in MANDATORY_RULES)
    assert 5 <= len(MANDATORY_RULES) <= 8


def test_rule_active_windows():
    rule = ClauseRule(
        rule_id="nsw.clause.example",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=date(2020, 1, 1),
        applies_to=date(2021, 1, 1),
        question="x",
    )
    assert not rule_active(rule, date(2019, 12, 31))
    assert rule_active(rule, date(2020, 6, 1))
    assert not rule_active(rule, date(2021, 1, 1))


@pytest.fixture
async def corpus_session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            act = (
                await session.execute(select(Act).where(Act.slug == "act-2010-042"))
            ).scalar_one_or_none()
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("corpus store not reachable")
        if act is None:
            pytest.skip("corpus not ingested")
        yield session
    await engine.dispose()


async def test_every_rule_resolves_on_the_corpus(corpus_session):
    for rule in PROHIBITED_RULES + MANDATORY_RULES:
        citation = await resolve_rule(corpus_session, rule, AS_AT)
        assert citation is not None, rule.rule_id
        assert citation.as_at == AS_AT


async def test_statutory_texts_dedupes_shared_sections(corpus_session):
    texts = await statutory_texts(corpus_session, PROHIBITED_RULES, AS_AT)
    refs = {(r.ref.act_slug, r.ref.section_no) for r in PROHIBITED_RULES}
    assert set(texts) == refs
    assert "Prohibited terms" in texts[("act-2010-042", "19")]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_clause_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: app.clause_audit.rules`.

- [ ] **Step 4: Implement**

`app/clause_audit/rules.py` — the three floor rules below are the shape; **replace and extend the lists from the Step 1 dumps**, pasting the operative statutory wording into each docstring/question. Template:

```python
"""Clause rules judged by the LLM.

Statutory basis pinned from the corpus on 2026-07-28; every rule's question
paraphrases the text in force and names the operative words. The lists are
fixed by the Step 1 dumps: one prohibited rule per s 19(2) paragraph and per
Regulation-prescribed term; 5-8 crisply decidable mandatory-content rules
from Act Part 2. Vague candidates go to docs/rule-candidates.md instead.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Act
from app.rules.base import Citation, SectionRef
from app.rules.nsw import COMMENCED
from app.services.legislation import section_at


@dataclass(frozen=True)
class ClauseRule:
    rule_id: str
    family: Literal["prohibited", "mandatory"]
    ref: SectionRef
    applies_from: date | None
    applies_to: date | None
    question: str


PROHIBITED_RULES = [
    ClauseRule(
        rule_id="nsw.clause.carpet_cleaning",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=COMMENCED,
        applies_to=None,
        question=(
            "A term with the effect that the tenant must have the carpet "
            "professionally cleaned, or pay the cost of that cleaning, at the "
            "end of the tenancy (s 19(2)(a)(i)). Paste the pinned carve-out "
            "wording here from the Step 1 dump before finalising."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.fumigation",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=COMMENCED,
        applies_to=None,
        question=(
            "A term with the effect that the tenant must have the premises "
            "professionally fumigated at the end of the tenancy "
            "(s 19(2)(a)(ii)). Paste the pinned carve-out wording here."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.specified_insurance",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=COMMENCED,
        applies_to=None,
        question=(
            "A term with the effect that the tenant must take out a specified, "
            "or any, form of insurance (s 19(2)(b))."
        ),
    ),
    # Extend: one rule per remaining s 19(2) paragraph and per
    # Regulation-prescribed term found in Step 1, applies_from per its
    # commencement window.
]

MANDATORY_RULES = [
    # Fill from the Step 1 ss 12-26 dump: 5-8 rules, each shaped like
    # ClauseRule(rule_id="nsw.clause.states_rent", family="mandatory",
    #            ref=SectionRef("act-2010-042", "<section>"), applies_from=COMMENCED,
    #            applies_to=None, question="The agreement must state ... (s <n>)"),
]


def rule_active(rule: ClauseRule, as_at: date) -> bool:
    if rule.applies_from and as_at < rule.applies_from:
        return False
    return not (rule.applies_to and as_at >= rule.applies_to)


async def resolve_rule(
    session: AsyncSession, rule: ClauseRule, as_at: date
) -> Citation | None:
    section = await section_at(session, rule.ref.act_slug, rule.ref.section_no, as_at)
    if section is None:
        return None
    act = await session.get(Act, section.act_id)
    return Citation(
        act=act.title, section_no=rule.ref.section_no, as_at=as_at, section_id=section.id
    )


async def statutory_texts(
    session: AsyncSession, rules: list[ClauseRule], as_at: date
) -> dict[tuple[str, str], str]:
    texts: dict[tuple[str, str], str] = {}
    for rule in rules:
        key = (rule.ref.act_slug, rule.ref.section_no)
        if key in texts:
            continue
        section = await section_at(session, key[0], key[1], as_at)
        if section is not None:
            texts[key] = f"{section.heading}\n{section.body_text}"
    return texts
```

The two `# Extend` / `# Fill` comments and the "Paste the pinned..." sentences are **instructions to complete in this task, not shippable content** — the committed file contains the finished lists with real pinned wording and no such markers. If the Regulation scan finds no prescribed terms, say so in the commit message; `MANDATORY_RULES` must ship 5-8 real rules.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_clause_rules.py -v`
Expected: PASS (corpus-backed tests run against the dev store; they skip only where the store is absent, e.g. CI).

- [ ] **Step 6: Update docs/rule-candidates.md**

Append a short "Mandatory-content survey (LLM clause audit)" section listing the excluded vague candidates with their sections and why they were excluded.

- [ ] **Step 7: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Pin and define the LLM clause rules" && git push origin main
```

---

### Task 5: Family runners — judgments, quote verification, field comparison

**Files:**
- Create: `app/clause_audit/verify.py`, `app/clause_audit/families.py`
- Modify: `tests/conftest.py` (FakeJudge fixture)
- Test: `tests/test_clause_families.py`

**Interfaces:**
- Consumes: `JudgeFn`, `family_output_model`, `FieldsOutput`, `clause_instruction`, `fields_instruction`, `DocumentInput`, the rules module (referenced as `rules.PROHIBITED_RULES` / `rules.MANDATORY_RULES` at call time so tests can monkeypatch), `ClauseFinding`, `Discrepancy`, `ClauseLeaseInput`.
- Produces: `run_prohibited(judge, session, doc, as_at) -> list[ClauseFinding]`; `run_mandatory(judge, session, doc, as_at) -> list[ClauseFinding]`; `run_fields(judge, doc, lease) -> list[Discrepancy]`; `verify.quote_matches(quote, document_text) -> bool`; conftest `fake_judge` fixture (`FakeJudge` with `.responses[output_model_name]`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/conftest.py`:

```python
class FakeJudge:
    """Canned judge: responses keyed by the output model's class name."""

    def __init__(self):
        self.responses = {}
        self.calls = []

    async def __call__(self, doc, instruction, output_model):
        self.calls.append((doc, instruction, output_model))
        return output_model.model_validate(self.responses[output_model.__name__])


@pytest.fixture
def fake_judge():
    return FakeJudge()
```

`tests/test_clause_families.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from app.clause_audit import rules as rules_module
from app.clause_audit.document import DocumentInput
from app.clause_audit.families import run_fields, run_mandatory, run_prohibited
from app.clause_audit.rules import ClauseRule
from app.clause_audit.verify import quote_matches
from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act
from app.rules.base import SectionRef
from app.schemas.clause_audit import ClauseLeaseInput

AS_AT = date(2026, 7, 28)
CARPET = "The tenant must have the carpet professionally cleaned at the end of the tenancy."
DOC = DocumentInput(kind="text", text=f"AGREEMENT. {CARPET} Rent is payable weekly.")

RULE = ClauseRule(
    rule_id="nsw.clause.carpet_cleaning",
    family="prohibited",
    ref=SectionRef("act-2010-042", "19"),
    applies_from=date(2011, 1, 31),
    applies_to=None,
    question="A term requiring professional carpet cleaning at the end of the tenancy.",
)


@pytest.fixture
async def seeded_s19(db_session):
    act = Act(
        jurisdiction="NSW",
        slug="act-2010-042",
        title="Residential Tenancies Act 2010",
        source_url="x",
    )
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2011, 1, 31),
        [ParsedSection("19", "Prohibited terms", "terms must not be included", "Part 2", None)],
    )
    await db_session.commit()


@pytest.fixture(autouse=True)
def single_rule(monkeypatch):
    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [RULE])
    monkeypatch.setattr(rules_module, "MANDATORY_RULES", [])


def _item(verdict, quote):
    return {
        "items": [
            {
                "rule_id": "nsw.clause.carpet_cleaning",
                "verdict": verdict,
                "reasoning": "because",
                "clause_quote": quote,
            }
        ]
    }


async def test_red_with_matching_quote(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = _item("red", CARPET)
    findings = await run_prohibited(fake_judge, db_session, DOC, AS_AT)
    assert findings[0].verdict == "red"
    assert findings[0].clause_quote == CARPET
    assert findings[0].citations[0].act == "Residential Tenancies Act 2010"


async def test_red_quote_not_in_document_downgrades(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = _item("red", "an invented sentence")
    findings = await run_prohibited(fake_judge, db_session, DOC, AS_AT)
    assert findings[0].verdict == "yellow"
    assert "quote" in findings[0].summary


async def test_red_without_quote_downgrades(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = _item("red", None)
    findings = await run_prohibited(fake_judge, db_session, DOC, AS_AT)
    assert findings[0].verdict == "yellow"


async def test_pdf_path_skips_quote_verification(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = _item("red", "anything at all")
    pdf_doc = DocumentInput(kind="pdf", pdf=b"%PDF-fake")
    findings = await run_prohibited(fake_judge, db_session, pdf_doc, AS_AT)
    assert findings[0].verdict == "red"


async def test_missing_item_is_yellow(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = {"items": []}
    findings = await run_prohibited(fake_judge, db_session, DOC, AS_AT)
    assert findings[0].verdict == "yellow"
    assert "did not report" in findings[0].summary


async def test_inactive_rule_is_skipped_without_judging(fake_judge, db_session, seeded_s19):
    early = await run_prohibited(fake_judge, db_session, DOC, date(2010, 1, 1))
    assert early[0].verdict == "skipped"
    assert fake_judge.calls == []


async def test_unresolvable_section_is_skipped(fake_judge, db_session, seeded_s19, monkeypatch):
    ghost = ClauseRule(
        rule_id="nsw.clause.ghost",
        family="prohibited",
        ref=SectionRef("act-2010-042", "999"),
        applies_from=date(2011, 1, 31),
        applies_to=None,
        question="x",
    )
    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [ghost])
    findings = await run_prohibited(fake_judge, db_session, DOC, AS_AT)
    assert findings[0].verdict == "skipped"
    assert fake_judge.calls == []


async def test_mandatory_red_needs_no_quote(fake_judge, db_session, seeded_s19, monkeypatch):
    mand = ClauseRule(
        rule_id="nsw.clause.states_rent",
        family="mandatory",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=date(2011, 1, 31),
        applies_to=None,
        question="The agreement must state the rent.",
    )
    monkeypatch.setattr(rules_module, "MANDATORY_RULES", [mand])
    fake_judge.responses["MandatoryOutput"] = {
        "items": [
            {
                "rule_id": "nsw.clause.states_rent",
                "verdict": "red",
                "reasoning": "absent",
                "clause_quote": None,
            }
        ]
    }
    findings = await run_mandatory(fake_judge, db_session, DOC, AS_AT)
    assert findings[0].verdict == "red" and findings[0].clause_quote is None


def _fields(items):
    return {"fields": items}


async def test_field_mismatch_reported(fake_judge):
    fake_judge.responses["FieldsOutput"] = _fields(
        [{"field": "rent_amount", "document_value": "$520 per week", "quote": "rent clause"}]
    )
    lease = ClauseLeaseInput(rent_amount=Decimal("560"))
    result = await run_fields(fake_judge, DOC, lease)
    assert result[0].field == "rent_amount"
    assert result[0].document_value == "$520 per week"
    assert result[0].submitted_value == "560"


async def test_field_match_and_absent_are_silent(fake_judge):
    fake_judge.responses["FieldsOutput"] = _fields(
        [
            {"field": "rent_amount", "document_value": "$560.00", "quote": "x"},
            {"field": "bond_amount", "document_value": None, "quote": None},
        ]
    )
    lease = ClauseLeaseInput(rent_amount=Decimal("560"), bond_amount=Decimal("2240"))
    assert await run_fields(fake_judge, DOC, lease) == []


async def test_date_and_frequency_normalisation(fake_judge):
    fake_judge.responses["FieldsOutput"] = _fields(
        [
            {"field": "start_date", "document_value": "1 February 2026", "quote": "x"},
            {"field": "rent_frequency", "document_value": "per fortnight", "quote": "x"},
        ]
    )
    lease = ClauseLeaseInput(start_date=date(2026, 2, 1), rent_frequency="weekly")
    result = await run_fields(fake_judge, DOC, lease)
    assert [d.field for d in result] == ["rent_frequency"]


async def test_unparseable_document_value_is_silent(fake_judge):
    fake_judge.responses["FieldsOutput"] = _fields(
        [{"field": "start_date", "document_value": "the usual date", "quote": "x"}]
    )
    lease = ClauseLeaseInput(start_date=date(2026, 2, 1))
    assert await run_fields(fake_judge, DOC, lease) == []


def test_quote_matches_normalises_whitespace_and_case():
    assert quote_matches("Carpet   Professionally\ncleaned", "the carpet professionally cleaned.")
    assert not quote_matches("fumigated", "the carpet professionally cleaned.")
    assert quote_matches("anything", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_clause_families.py -v`
Expected: FAIL with `ModuleNotFoundError: app.clause_audit.families`.

- [ ] **Step 3: Implement**

`app/clause_audit/verify.py`:

```python
"""Deterministic checks on model output: quotes and field comparison."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y")
FREQUENCY_WORDS = {"fortnightly": "fortnight", "monthly": "month", "weekly": "week"}


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def quote_matches(quote: str, document_text: str | None) -> bool:
    """True when the quote appears in the document; PDF path (no text) passes."""
    if document_text is None:
        return True
    return _normalise(quote) in _normalise(document_text)


def parse_amount(value: str) -> Decimal | None:
    cleaned = value.replace("$", "").replace(",", "").replace("AUD", "").strip()
    cleaned = cleaned.split(" ")[0] if cleaned else cleaned
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(value: str) -> date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_frequency(value: str) -> str | None:
    low = value.casefold()
    for name, word in FREQUENCY_WORDS.items():
        if word in low:
            return name
    return None
```

`app/clause_audit/families.py`:

```python
"""The three check families: judge, verify, assemble findings."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.clause_audit import rules as clause_rules
from app.clause_audit.document import DocumentInput
from app.clause_audit.verify import parse_amount, parse_date, parse_frequency, quote_matches
from app.llm.client import JudgeFn
from app.llm.prompts import clause_instruction, fields_instruction
from app.llm.schemas import FieldsOutput, family_output_model
from app.schemas.clause_audit import ClauseFinding, ClauseLeaseInput, Discrepancy

DATE_FIELDS = {"start_date", "end_date"}


async def _run_clause_family(
    judge: JudgeFn,
    session: AsyncSession,
    doc: DocumentInput,
    as_at: date,
    rules: list[clause_rules.ClauseRule],
    family_name: str,
    output_name: str,
    require_quote_on_red: bool,
) -> list[ClauseFinding]:
    findings: list[ClauseFinding] = []
    active: list[tuple[clause_rules.ClauseRule, object]] = []
    for rule in rules:
        if not clause_rules.rule_active(rule, as_at):
            findings.append(
                ClauseFinding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Rule not active at the audit date.",
                    skip_reason=f"rule not active at {as_at}",
                )
            )
            continue
        citation = await clause_rules.resolve_rule(session, rule, as_at)
        if citation is None:
            findings.append(
                ClauseFinding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Statutory basis not in force at the audit date.",
                    skip_reason=f"section {rule.ref.section_no} not in force at {as_at}",
                )
            )
            continue
        active.append((rule, citation))
    if not active:
        return findings

    sections = await clause_rules.statutory_texts(session, [r for r, _ in active], as_at)
    instruction = clause_instruction(
        family_name, as_at, sections, [(r.rule_id, r.question) for r, _ in active]
    )
    output_model = family_output_model(output_name, [r.rule_id for r, _ in active])
    result = await judge(doc, instruction, output_model)
    by_id = {str(item.rule_id): item for item in result.items}

    for rule, citation in active:
        item = by_id.get(rule.rule_id)
        if item is None:
            findings.append(
                ClauseFinding(
                    rule_id=rule.rule_id,
                    verdict="yellow",
                    summary="The model did not report on this rule.",
                    citations=[citation],
                )
            )
            continue
        verdict, summary, quote = item.verdict, item.reasoning, item.clause_quote
        if verdict == "red" and require_quote_on_red:
            if quote is None:
                verdict, summary = "yellow", "Downgraded: red verdict carried no quote."
            elif not quote_matches(quote, doc.text):
                verdict = "yellow"
                summary = "Downgraded: quoted text was not found in the document."
        findings.append(
            ClauseFinding(
                rule_id=rule.rule_id,
                verdict=verdict,
                summary=summary,
                evidence={"reasoning": item.reasoning},
                citations=[citation],
                clause_quote=quote,
            )
        )
    return findings


async def run_prohibited(
    judge: JudgeFn, session: AsyncSession, doc: DocumentInput, as_at: date
) -> list[ClauseFinding]:
    return await _run_clause_family(
        judge,
        session,
        doc,
        as_at,
        clause_rules.PROHIBITED_RULES,
        "prohibited terms",
        "ProhibitedOutput",
        require_quote_on_red=True,
    )


async def run_mandatory(
    judge: JudgeFn, session: AsyncSession, doc: DocumentInput, as_at: date
) -> list[ClauseFinding]:
    return await _run_clause_family(
        judge,
        session,
        doc,
        as_at,
        clause_rules.MANDATORY_RULES,
        "mandatory terms",
        "MandatoryOutput",
        require_quote_on_red=False,
    )


def _mismatch(field: str, document_value: str, submitted) -> bool:
    if field in DATE_FIELDS:
        parsed = parse_date(document_value)
        return parsed is not None and parsed != submitted
    if field == "rent_frequency":
        parsed = parse_frequency(document_value)
        return parsed is not None and parsed != submitted
    parsed = parse_amount(document_value)
    return parsed is not None and parsed != submitted


async def run_fields(
    judge: JudgeFn, doc: DocumentInput, lease: ClauseLeaseInput
) -> list[Discrepancy]:
    result = await judge(doc, fields_instruction(), FieldsOutput)
    discrepancies: list[Discrepancy] = []
    for item in result.fields:
        submitted = getattr(lease, item.field)
        if submitted is None or item.document_value is None:
            continue
        if _mismatch(item.field, item.document_value, submitted):
            discrepancies.append(
                Discrepancy(
                    field=item.field,
                    document_value=item.document_value,
                    submitted_value=str(submitted),
                )
            )
    return discrepancies
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_clause_families.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add the clause audit family runners" && git push origin main
```

---

### Task 6: Job processor

**Files:**
- Create: `app/clause_audit/processor.py`
- Test: `tests/test_clause_processor.py`

**Interfaces:**
- Consumes: `document_input`, `run_prohibited`, `run_mandatory`, `run_fields`, `ClauseAuditJob`, `ClauseLeaseInput`, `JudgeFn`.
- Produces: `process_job(session, job, judge) -> None` — mutates the job to `succeeded` with results and `document = None`; commits nothing (the worker owns the commit).

- [ ] **Step 1: Write the failing tests**

`tests/test_clause_processor.py`:

```python
from datetime import date

import pytest

from app.clause_audit import rules as rules_module
from app.clause_audit.processor import process_job
from app.clause_audit.rules import ClauseRule
from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act, ClauseAuditJob
from app.rules.base import SectionRef

AS_AT = date(2026, 7, 28)
CARPET = "The tenant must have the carpet professionally cleaned at the end of the tenancy."

RULE = ClauseRule(
    rule_id="nsw.clause.carpet_cleaning",
    family="prohibited",
    ref=SectionRef("act-2010-042", "19"),
    applies_from=date(2011, 1, 31),
    applies_to=None,
    question="A term requiring professional carpet cleaning at the end of the tenancy.",
)


@pytest.fixture(autouse=True)
def single_rule(monkeypatch):
    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [RULE])
    monkeypatch.setattr(rules_module, "MANDATORY_RULES", [])


@pytest.fixture
async def seeded_s19(db_session):
    act = Act(
        jurisdiction="NSW",
        slug="act-2010-042",
        title="Residential Tenancies Act 2010",
        source_url="x",
    )
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2011, 1, 31),
        [ParsedSection("19", "Prohibited terms", "terms must not be included", "Part 2", None)],
    )
    await db_session.commit()


def _job(**overrides) -> ClauseAuditJob:
    values = {
        "client_id": "testco",
        "jurisdiction": "NSW",
        "as_at": AS_AT,
        "document": f"AGREEMENT. {CARPET}".encode(),
        "document_kind": "text",
        "status": "running",
        "engine_version": "1.1.0",
        "model": "claude-opus-4-8",
    }
    values.update(overrides)
    return ClauseAuditJob(**values)


RED = {
    "items": [
        {
            "rule_id": "nsw.clause.carpet_cleaning",
            "verdict": "red",
            "reasoning": "found",
            "clause_quote": CARPET,
        }
    ]
}


async def test_process_job_succeeds_and_wipes(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = RED
    job = _job()
    db_session.add(job)
    await db_session.commit()

    await process_job(db_session, job, fake_judge)

    assert job.status == "succeeded"
    assert job.document is None
    assert job.completed_at is not None
    assert job.findings[0]["verdict"] == "red"
    assert job.discrepancies == []


async def test_process_job_runs_fields_only_with_lease(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = RED
    fake_judge.responses["FieldsOutput"] = {
        "fields": [{"field": "rent_amount", "document_value": "$520", "quote": "x"}]
    }
    job = _job(lease={"rent_amount": "560"})
    db_session.add(job)
    await db_session.commit()

    await process_job(db_session, job, fake_judge)

    assert job.discrepancies == [
        {"field": "rent_amount", "document_value": "$520", "submitted_value": "560"}
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_clause_processor.py -v`
Expected: FAIL with `ModuleNotFoundError: app.clause_audit.processor`.

- [ ] **Step 3: Implement**

`app/clause_audit/processor.py`:

```python
"""Run one claimed job end to end and wipe the document. Caller commits."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.clause_audit.document import document_input
from app.clause_audit.families import run_fields, run_mandatory, run_prohibited
from app.llm.client import JudgeFn
from app.models import ClauseAuditJob
from app.schemas.clause_audit import ClauseLeaseInput


async def process_job(session: AsyncSession, job: ClauseAuditJob, judge: JudgeFn) -> None:
    doc = document_input(job.document_kind, job.document)
    findings = await run_prohibited(judge, session, doc, job.as_at)
    findings += await run_mandatory(judge, session, doc, job.as_at)
    discrepancies = []
    if job.lease is not None:
        lease = ClauseLeaseInput.model_validate(job.lease)
        discrepancies = await run_fields(judge, doc, lease)
    job.findings = [f.model_dump(mode="json") for f in findings]
    job.discrepancies = [d.model_dump(mode="json") for d in discrepancies]
    job.status = "succeeded"
    job.completed_at = datetime.now(UTC)
    job.document = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_clause_processor.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add the clause audit job processor" && git push origin main
```

---

### Task 7: API router, worker loop, lifespan wiring

**Files:**
- Create: `app/routers/clause_audits.py`, `app/clause_audit/worker.py`
- Modify: `app/main.py`, `app/rules/__init__.py` (ENGINE_VERSION -> "1.1.0")
- Test: `tests/test_clause_api.py`, `tests/test_clause_worker.py`

**Interfaces:**
- Consumes: everything above; `require_api_key`, `get_session`, `sydney_today`, `async_session_factory`, `clause_audit_enabled`, `settings`, `ENGINE_VERSION`.
- Produces: routes `POST /v1/clause-audits` (202), `GET /v1/clause-audits/{id}`, `GET /v1/clause-audits?client_ref=`; `worker.sweep_stale(session_factory)`, `worker.claim_next(session)`, `worker.run_once(judge, session_factory) -> bool`, `worker.worker_loop(judge, session_factory)`; constants `POLL_SECONDS = 2`, `JOB_TIMEOUT_SECONDS = 900`, `MAX_PDF_BYTES = 10 * 1024 * 1024`, `MAX_TEXT_CHARS = 200_000`.

- [ ] **Step 1: Add python-multipart**

```bash
uv add python-multipart
```

- [ ] **Step 2: Write the failing API tests**

`tests/test_clause_api.py`:

```python
import json
import uuid

import pytest

from app.core.config import settings
from app.models import ClauseAuditJob

KEY = {"X-API-Key": "test-key"}
OTHER = {"X-API-Key": "other-key"}
PAYLOAD = json.dumps({"jurisdiction": "NSW", "client_ref": "lease-9"})


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "test-key:testco,other-key:otherco")
    monkeypatch.setattr(settings, "anthropic_api_key", "unit-test-key")


async def test_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    response = await client.post(
        "/v1/clause-audits", data={"payload": PAYLOAD, "text": "lease"}, headers=KEY
    )
    assert response.status_code == 503


async def test_missing_key_is_401(client):
    response = await client.post("/v1/clause-audits", data={"payload": PAYLOAD, "text": "x"})
    assert response.status_code == 401


async def test_invalid_payload_is_422(client):
    response = await client.post(
        "/v1/clause-audits", data={"payload": "not json", "text": "x"}, headers=KEY
    )
    assert response.status_code == 422


async def test_neither_or_both_inputs_is_422(client):
    neither = await client.post("/v1/clause-audits", data={"payload": PAYLOAD}, headers=KEY)
    both = await client.post(
        "/v1/clause-audits",
        data={"payload": PAYLOAD, "text": "x"},
        files={"file": ("l.pdf", b"%PDF-", "application/pdf")},
        headers=KEY,
    )
    assert neither.status_code == 422 and both.status_code == 422


async def test_oversize_text_is_413(client):
    response = await client.post(
        "/v1/clause-audits", data={"payload": PAYLOAD, "text": "x" * 200_001}, headers=KEY
    )
    assert response.status_code == 413


async def test_oversize_file_is_413(client):
    big = b"x" * (10 * 1024 * 1024 + 1)
    response = await client.post(
        "/v1/clause-audits",
        data={"payload": PAYLOAD},
        files={"file": ("l.pdf", big, "application/pdf")},
        headers=KEY,
    )
    assert response.status_code == 413


async def test_create_get_and_isolation(client, db_session):
    created = await client.post(
        "/v1/clause-audits", data={"payload": PAYLOAD, "text": "lease body"}, headers=KEY
    )
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "pending" and body["client_ref"] == "lease-9"
    assert body["model"] == settings.clause_audit_model

    job = await db_session.get(ClauseAuditJob, uuid.UUID(body["id"]))
    assert job.document == b"lease body" and job.document_kind == "text"

    fetched = await client.get(f"/v1/clause-audits/{body['id']}", headers=KEY)
    assert fetched.status_code == 200 and fetched.json()["status"] == "pending"

    foreign = await client.get(f"/v1/clause-audits/{body['id']}", headers=OTHER)
    assert foreign.status_code == 404

    listed = await client.get("/v1/clause-audits", params={"client_ref": "lease-9"}, headers=KEY)
    assert [row["id"] for row in listed.json()] == [body["id"]]
```

- [ ] **Step 3: Write the failing worker tests**

`tests/test_clause_worker.py`:

```python
import asyncio
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.clause_audit import rules as rules_module
from app.clause_audit import worker
from app.clause_audit.rules import ClauseRule
from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act, ClauseAuditJob
from app.rules.base import SectionRef

AS_AT = date(2026, 7, 28)
CARPET = "The tenant must have the carpet professionally cleaned at the end of the tenancy."

RULE = ClauseRule(
    rule_id="nsw.clause.carpet_cleaning",
    family="prohibited",
    ref=SectionRef("act-2010-042", "19"),
    applies_from=date(2011, 1, 31),
    applies_to=None,
    question="A term requiring professional carpet cleaning at the end of the tenancy.",
)


@pytest.fixture(autouse=True)
def single_rule(monkeypatch):
    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [RULE])
    monkeypatch.setattr(rules_module, "MANDATORY_RULES", [])


@pytest.fixture
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def seeded_s19(session_factory):
    async with session_factory() as session:
        act = Act(
            jurisdiction="NSW",
            slug="act-2010-042",
            title="Residential Tenancies Act 2010",
            source_url="x",
        )
        session.add(act)
        await session.flush()
        await load_version(
            session,
            act.id,
            date(2011, 1, 31),
            [ParsedSection("19", "Prohibited terms", "terms body", "Part 2", None)],
        )
        await session.commit()


def _job(**overrides) -> ClauseAuditJob:
    values = {
        "client_id": "testco",
        "jurisdiction": "NSW",
        "as_at": AS_AT,
        "document": f"AGREEMENT. {CARPET}".encode(),
        "document_kind": "text",
        "engine_version": "1.1.0",
        "model": "claude-opus-4-8",
    }
    values.update(overrides)
    return ClauseAuditJob(**values)


RED = {
    "items": [
        {
            "rule_id": "nsw.clause.carpet_cleaning",
            "verdict": "red",
            "reasoning": "found",
            "clause_quote": CARPET,
        }
    ]
}


async def _add(session_factory, job):
    async with session_factory() as session:
        session.add(job)
        await session.commit()
        return job.id


async def _fetch(session_factory, job_id):
    async with session_factory() as session:
        return await session.get(ClauseAuditJob, job_id)


async def test_run_once_processes_oldest_pending(fake_judge, session_factory, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = RED
    job_id = await _add(session_factory, _job())

    assert await worker.run_once(fake_judge, session_factory) is True
    row = await _fetch(session_factory, job_id)
    assert row.status == "succeeded" and row.document is None
    assert row.findings[0]["rule_id"] == "nsw.clause.carpet_cleaning"

    assert await worker.run_once(fake_judge, session_factory) is False


async def test_run_once_failure_marks_failed_and_wipes(session_factory, seeded_s19):
    async def broken_judge(doc, instruction, output_model):
        raise RuntimeError("model exploded")

    job_id = await _add(session_factory, _job())
    assert await worker.run_once(broken_judge, session_factory) is True
    row = await _fetch(session_factory, job_id)
    assert row.status == "failed" and row.document is None
    assert "model exploded" in row.error


async def test_run_once_timeout_marks_failed(session_factory, seeded_s19, monkeypatch):
    async def slow_judge(doc, instruction, output_model):
        await asyncio.sleep(1)

    monkeypatch.setattr(worker, "JOB_TIMEOUT_SECONDS", 0.01)
    job_id = await _add(session_factory, _job())
    assert await worker.run_once(slow_judge, session_factory) is True
    row = await _fetch(session_factory, job_id)
    assert row.status == "failed" and "timed out" in row.error


async def test_sweep_stale_fails_running_jobs(session_factory):
    job_id = await _add(session_factory, _job(status="running"))
    pending_id = await _add(session_factory, _job())
    await worker.sweep_stale(session_factory)
    stale = await _fetch(session_factory, job_id)
    assert stale.status == "failed" and stale.document is None
    assert "restart" in stale.error
    assert (await _fetch(session_factory, pending_id)).status == "pending"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_clause_api.py tests/test_clause_worker.py -v`
Expected: FAIL with `ModuleNotFoundError` (router, then worker).

- [ ] **Step 5: Implement**

`app/rules/__init__.py`:

```python
from app.rules.nsw import NSW_RULES

ENGINE_VERSION = "1.1.0"
ALL_RULES = [*NSW_RULES]
```

`app/clause_audit/worker.py`:

```python
"""Claim pending clause-audit jobs and process them one at a time."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.clause_audit.processor import process_job
from app.core.db import async_session_factory
from app.llm.client import JudgeFn
from app.models import ClauseAuditJob

POLL_SECONDS = 2
JOB_TIMEOUT_SECONDS = 900


async def sweep_stale(session_factory=async_session_factory) -> None:
    """Fail jobs left running by a dead process; pending jobs survive untouched."""
    async with session_factory() as session:
        query = select(ClauseAuditJob).where(ClauseAuditJob.status == "running")
        for job in (await session.execute(query)).scalars().all():
            job.status = "failed"
            job.error = "interrupted by restart"
            job.document = None
            job.completed_at = datetime.now(UTC)
        await session.commit()


async def claim_next(session) -> ClauseAuditJob | None:
    query = (
        select(ClauseAuditJob)
        .where(ClauseAuditJob.status == "pending")
        .order_by(ClauseAuditJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(query)).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.started_at = datetime.now(UTC)
    await session.commit()
    return job


async def run_once(judge: JudgeFn, session_factory=async_session_factory) -> bool:
    """Process at most one job; True when a job was claimed."""
    async with session_factory() as session:
        job = await claim_next(session)
        if job is None:
            return False
        job_id = job.id
        try:
            await asyncio.wait_for(process_job(session, job, judge), JOB_TIMEOUT_SECONDS)
            await session.commit()
        except TimeoutError:
            await _fail(session, job_id, "job timed out")
        except Exception as exc:
            await _fail(session, job_id, str(exc))
        return True


async def _fail(session, job_id, error: str) -> None:
    await session.rollback()
    job = await session.get(ClauseAuditJob, job_id)
    job.status = "failed"
    job.error = error
    job.document = None
    job.completed_at = datetime.now(UTC)
    await session.commit()


async def worker_loop(judge: JudgeFn, session_factory=async_session_factory) -> None:
    while True:
        processed = await run_once(judge, session_factory)
        if not processed:
            await asyncio.sleep(POLL_SECONDS)
```

`app/routers/clause_audits.py`:

```python
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.config import clause_audit_enabled, settings
from app.core.dates import sydney_today
from app.core.db import get_session
from app.models import ClauseAuditJob
from app.rules import ENGINE_VERSION
from app.schemas.clause_audit import ClauseAuditCreate, ClauseAuditInfo

router = APIRouter(prefix="/v1")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClientDep = Annotated[str, Depends(require_api_key)]

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 200_000


def _info(job: ClauseAuditJob) -> ClauseAuditInfo:
    return ClauseAuditInfo(
        id=job.id,
        status=job.status,
        jurisdiction=job.jurisdiction,
        as_at=job.as_at,
        engine_version=job.engine_version,
        model=job.model,
        client_ref=job.client_ref,
        findings=job.findings,
        discrepancies=job.discrepancies,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/clause-audits", status_code=202, response_model=ClauseAuditInfo)
async def create_clause_audit(
    client_id: ClientDep,
    session: SessionDep,
    payload: Annotated[str, Form()],
    file: Annotated[UploadFile | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
) -> ClauseAuditInfo:
    if not clause_audit_enabled():
        raise HTTPException(status_code=503, detail="Clause audit is not configured")
    try:
        body = ClauseAuditCreate.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if (file is None) == (text is None):
        raise HTTPException(status_code=422, detail="Provide exactly one of file or text")
    if text is not None:
        if len(text) > MAX_TEXT_CHARS:
            raise HTTPException(status_code=413, detail="Text too large")
        document, kind = text.encode("utf-8"), "text"
    else:
        document = await file.read()
        if len(document) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="File too large")
        kind = "pdf"
    job = ClauseAuditJob(
        client_id=client_id,
        client_ref=body.client_ref,
        jurisdiction=body.jurisdiction,
        as_at=body.as_at or sydney_today(),
        document=document,
        document_kind=kind,
        lease=body.lease.model_dump(mode="json") if body.lease else None,
        engine_version=ENGINE_VERSION,
        model=settings.clause_audit_model,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return _info(job)


@router.get("/clause-audits/{job_id}", response_model=ClauseAuditInfo)
async def get_clause_audit(
    job_id: uuid.UUID, client_id: ClientDep, session: SessionDep
) -> ClauseAuditInfo:
    job = await session.get(ClauseAuditJob, job_id)
    if job is None or job.client_id != client_id:
        raise HTTPException(status_code=404, detail="Clause audit not found")
    return _info(job)


@router.get("/clause-audits", response_model=list[ClauseAuditInfo])
async def list_clause_audits(
    client_ref: str, client_id: ClientDep, session: SessionDep, limit: int = 20
) -> list[ClauseAuditInfo]:
    query = (
        select(ClauseAuditJob)
        .where(ClauseAuditJob.client_id == client_id, ClauseAuditJob.client_ref == client_ref)
        .order_by(ClauseAuditJob.created_at.desc(), ClauseAuditJob.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(query)).scalars().all()
    return [_info(row) for row in rows]
```

`app/main.py`:

```python
import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.clause_audit.worker import sweep_stale, worker_loop
from app.core.config import clause_audit_enabled
from app.llm.client import make_judge
from app.routers.audits import router as audits_router
from app.routers.changes import router as changes_router
from app.routers.clause_audits import router as clause_audits_router
from app.routers.legislation import router as legislation_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if clause_audit_enabled():
        await sweep_stale()
        task = asyncio.create_task(worker_loop(make_judge()))
    yield
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Lease Compliance Service",
    description="General information, not legal advice.",
    lifespan=lifespan,
)
app.include_router(audits_router)
app.include_router(changes_router)
app.include_router(clause_audits_router)
app.include_router(legislation_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
```

(The test client's ASGITransport does not run lifespan, so tests never start the real worker; worker tests drive `run_once` directly with the test session factory.)

- [ ] **Step 6: Add the end-to-end mock smoke test**

Append to `tests/test_clause_api.py`:

```python
async def test_post_worker_get_end_to_end(client, db_engine, fake_judge, monkeypatch):
    from datetime import date

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.clause_audit import rules as rules_module
    from app.clause_audit import worker
    from app.clause_audit.rules import ClauseRule
    from app.ingest.loader import load_version
    from app.ingest.parser import ParsedSection
    from app.models import Act
    from app.rules.base import SectionRef

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        act = Act(
            jurisdiction="NSW",
            slug="act-2010-042",
            title="Residential Tenancies Act 2010",
            source_url="x",
        )
        session.add(act)
        await session.flush()
        await load_version(
            session,
            act.id,
            date(2011, 1, 31),
            [ParsedSection("19", "Prohibited terms", "terms body", "Part 2", None)],
        )
        await session.commit()

    carpet = "The tenant must have the carpet professionally cleaned at the end of the tenancy."
    rule = ClauseRule(
        rule_id="nsw.clause.carpet_cleaning",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=date(2011, 1, 31),
        applies_to=None,
        question="A term requiring professional carpet cleaning.",
    )
    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [rule])
    monkeypatch.setattr(rules_module, "MANDATORY_RULES", [])
    fake_judge.responses["ProhibitedOutput"] = {
        "items": [
            {
                "rule_id": "nsw.clause.carpet_cleaning",
                "verdict": "red",
                "reasoning": "found",
                "clause_quote": carpet,
            }
        ]
    }

    created = await client.post(
        "/v1/clause-audits",
        data={"payload": PAYLOAD, "text": f"AGREEMENT. {carpet}"},
        headers=KEY,
    )
    assert created.status_code == 202

    assert await worker.run_once(fake_judge, factory) is True

    done = await client.get(f"/v1/clause-audits/{created.json()['id']}", headers=KEY)
    body = done.json()
    assert body["status"] == "succeeded"
    assert body["findings"][0]["verdict"] == "red"
    assert body["findings"][0]["clause_quote"] == carpet
    assert body["findings"][0]["citations"][0]["act"] == "Residential Tenancies Act 2010"
```

- [ ] **Step 7: Run all new tests**

Run: `uv run pytest tests/test_clause_api.py tests/test_clause_worker.py -v`
Expected: PASS.

- [ ] **Step 8: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add the clause audit API, worker and lifespan wiring" && git push origin main
```

---

### Task 8: Golden sets, eval harness, PDF smoke

**Files:**
- Create: `tests/golden/clauses.py`, `tests/test_llm_eval.py`
- Modify: `pyproject.toml` (markers + default deselect)
- Test: the eval itself (opt-in) plus a CI-safe structural test of the golden data

**Interfaces:**
- Consumes: `run_prohibited`, `run_mandatory`, `run_fields`, `make_judge`, `document_input`, `make_text_pdf`, `make_scanned_pdf`, `DocumentInput`, `ClauseLeaseInput`, the corpus-session skip pattern.
- Produces: `ClauseCase(case_id, rule_id, text, expected)`; `PROHIBITED_CASES`, `MANDATORY_CASES: list[ClauseCase]`; `FieldCase(case_id, text, lease: dict, expected: set[str])`; `FIELD_CASES`; `THRESHOLDS: dict[str, tuple[float, float]]` with a `"default"` entry `(0.9, 0.8)`.

- [ ] **Step 1: Register the marker and default deselect**

`pyproject.toml` — replace the `[tool.pytest.ini_options]` table:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["llm_eval: real-model eval; costs money, run explicitly with -m llm_eval"]
addopts = "-m 'not llm_eval'"
```

(`uv run pytest -m llm_eval` overrides the addopts deselect — the last `-m` wins.)

- [ ] **Step 2: Write the golden data**

`tests/golden/clauses.py`. The prohibited cases below for the three floor rules are final content; **add one block per rule you added in Task 4** (both prohibited and mandatory), following exactly this shape, in the same commit. The two carve-out cases marked `verify against s 19` must be checked against the Task 4 pinned text: if the statute does not carve out the animal case, flip their `expected` to `"red"`.

```python
"""Seeded golden sets for the LLM clause audit, one entry per rule per case.

Scoring contract: for every case, the target rule's expected verdict is
case.expected and every other rule's expected verdict is green. yellow on a
red case is a recall miss; red on a green case is a precision hit against
the judging rule.
"""

from dataclasses import dataclass, field
from typing import Literal

THRESHOLDS: dict[str, tuple[float, float]] = {"default": (0.9, 0.8)}


@dataclass(frozen=True)
class ClauseCase:
    case_id: str
    rule_id: str
    text: str
    expected: Literal["red", "green"]


@dataclass(frozen=True)
class FieldCase:
    case_id: str
    text: str
    lease: dict
    expected: set[str] = field(default_factory=set)


_PREAMBLE = "RESIDENTIAL TENANCY AGREEMENT between landlord and tenant. "

PROHIBITED_CASES = [
    ClauseCase(
        "carpet-red-plain",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE
        + "The tenant must have all carpets professionally steam cleaned at the "
        "conclusion of the tenancy and provide a receipt to the landlord.",
        "red",
    ),
    ClauseCase(
        "carpet-red-cost",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE
        + "On termination of this agreement the tenant agrees to pay the cost of "
        "professional carpet cleaning of the premises.",
        "red",
    ),
    ClauseCase(
        "carpet-red-paraphrase",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE
        + "Upon vacating, the floor coverings are to be cleaned by an accredited "
        "professional cleaning company engaged and paid for by the tenant, "
        "receipts to be produced on request.",
        "red",
    ),
    ClauseCase(
        "carpet-red-buried",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE
        + "The tenant must keep the premises reasonably clean. The rent is payable "
        "fortnightly in advance. The tenant shall arrange professional carpet "
        "cleaning at the end of the tenancy at the tenant's expense. Keys must be "
        "returned on the final day.",
        "red",
    ),
    ClauseCase(
        "carpet-green-ordinary-cleaning",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE
        + "The tenant must keep the carpets clean and vacuum them regularly during "
        "the tenancy.",
        "green",
    ),
    ClauseCase(
        "carpet-green-reasonably-clean",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE
        + "At the end of the tenancy the tenant must leave the premises reasonably "
        "clean, having regard to their condition at the commencement of the "
        "tenancy.",
        "green",
    ),
    ClauseCase(
        "carpet-green-landlord-pays",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE
        + "The landlord will arrange and pay for professional carpet cleaning "
        "before the tenant takes possession.",
        "green",
    ),
    ClauseCase(
        "carpet-green-animal-carveout",  # verify against s 19: permitted where an animal was kept
        "nsw.clause.carpet_cleaning",
        _PREAMBLE
        + "If the tenant keeps an animal on the premises with the landlord's "
        "consent, the tenant agrees to have the carpets professionally cleaned at "
        "the end of the tenancy.",
        "green",
    ),
    ClauseCase(
        "fumigation-red-plain",
        "nsw.clause.fumigation",
        _PREAMBLE
        + "On vacating the premises the tenant must have the premises "
        "professionally fumigated at the tenant's cost.",
        "red",
    ),
    ClauseCase(
        "fumigation-red-paraphrase",
        "nsw.clause.fumigation",
        _PREAMBLE
        + "The tenant shall engage a licensed pest control contractor to fumigate "
        "the property at the end of the tenancy and bear the expense of doing so.",
        "red",
    ),
    ClauseCase(
        "fumigation-red-buried",
        "nsw.clause.fumigation",
        _PREAMBLE
        + "The rent is payable weekly in advance. Keys must be returned on the "
        "final day. Prior to returning possession the tenant will arrange, at "
        "the tenant's own cost, fumigation of the premises by a professional "
        "operator.",
        "red",
    ),
    ClauseCase(
        "fumigation-green-notify",
        "nsw.clause.fumigation",
        _PREAMBLE
        + "The tenant must promptly notify the landlord of any pest or vermin "
        "infestation observed at the premises.",
        "green",
    ),
    ClauseCase(
        "fumigation-green-landlord-arranges",
        "nsw.clause.fumigation",
        _PREAMBLE
        + "The landlord will arrange and pay for pest treatment of the premises "
        "before the commencement of the tenancy.",
        "green",
    ),
    ClauseCase(
        "fumigation-green-animal-carveout",  # verify against s 19: permitted where an animal was kept
        "nsw.clause.fumigation",
        _PREAMBLE
        + "If the tenant has kept an animal on the premises, the premises are to "
        "be professionally fumigated at the end of the tenancy at the tenant's "
        "expense.",
        "green",
    ),
    ClauseCase(
        "insurance-red-specified",
        "nsw.clause.specified_insurance",
        _PREAMBLE
        + "The tenant must take out and maintain contents insurance with AAMI for "
        "the duration of the tenancy.",
        "red",
    ),
    ClauseCase(
        "insurance-red-any",
        "nsw.clause.specified_insurance",
        _PREAMBLE
        + "The tenant is required to obtain public liability insurance from an "
        "insurer nominated by the landlord before taking possession.",
        "red",
    ),
    ClauseCase(
        "insurance-green-encouraged",
        "nsw.clause.specified_insurance",
        _PREAMBLE
        + "The tenant is encouraged to consider taking out contents insurance for "
        "their own belongings.",
        "green",
    ),
    ClauseCase(
        "insurance-red-maintain",
        "nsw.clause.specified_insurance",
        _PREAMBLE
        + "Throughout the term the tenant shall maintain a home contents policy "
        "of insurance and provide the certificate of currency to the agent "
        "annually.",
        "red",
    ),
    ClauseCase(
        "insurance-green-landlord-holds",
        "nsw.clause.specified_insurance",
        _PREAMBLE
        + "The landlord holds building and landlord insurance in respect of the "
        "premises.",
        "green",
    ),
    ClauseCase(
        "insurance-green-not-covered-notice",
        "nsw.clause.specified_insurance",
        _PREAMBLE
        + "The tenant acknowledges that the tenant's personal belongings are not "
        "covered by the landlord's insurance policies.",
        "green",
    ),
]

# Add per-rule blocks for every rule added in Task 4, same shape as above.
MANDATORY_CASES: list[ClauseCase] = []

FIELD_CASES = [
    FieldCase(
        "fields-rent-mismatch",
        _PREAMBLE + "The rent is $520 per week payable weekly in advance. The bond "
        "is $2,240. The term commences on 1 February 2026.",
        {"rent_amount": "560", "rent_frequency": "weekly", "bond_amount": "2240"},
        {"rent_amount"},
    ),
    FieldCase(
        "fields-all-match",
        _PREAMBLE + "The rent is $560 per week payable weekly in advance. The bond "
        "is $2,240. The term commences on 1 February 2026 and ends on 31 January "
        "2027.",
        {
            "rent_amount": "560",
            "rent_frequency": "weekly",
            "bond_amount": "2240",
            "start_date": "2026-02-01",
            "end_date": "2027-01-31",
        },
        set(),
    ),
    FieldCase(
        "fields-frequency-mismatch",
        _PREAMBLE + "The rent is $1,120 payable per fortnight in advance.",
        {"rent_amount": "1120", "rent_frequency": "weekly"},
        {"rent_frequency"},
    ),
    FieldCase(
        "fields-date-mismatch",
        _PREAMBLE + "The tenancy commences on 15 March 2026. The rent is $560 per "
        "week.",
        {"rent_amount": "560", "start_date": "2026-02-01"},
        {"start_date"},
    ),
    FieldCase(
        "fields-absent-field-silent",
        _PREAMBLE + "The rent is $560 per week payable weekly.",
        {"rent_amount": "560", "holding_deposit_amount": "560"},
        set(),
    ),
]
```

- [ ] **Step 3: Write the harness and smoke tests**

`tests/test_llm_eval.py`:

```python
"""Real-model evals. Opt in with: uv run pytest -m llm_eval

Needs the dev corpus store and settings.anthropic_api_key. Every test prints
a per-rule precision/recall table; thresholds come from THRESHOLDS.
"""

from collections import defaultdict
from datetime import date

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clause_audit.document import DocumentInput, document_input
from app.clause_audit.families import run_fields, run_mandatory, run_prohibited
from app.clause_audit.rules import MANDATORY_RULES, PROHIBITED_RULES
from app.core.config import settings
from app.llm.client import make_judge
from app.models import Act
from app.schemas.clause_audit import ClauseLeaseInput
from tests.fixtures.pdfs import make_scanned_pdf, make_text_pdf
from tests.golden.clauses import FIELD_CASES, MANDATORY_CASES, PROHIBITED_CASES, THRESHOLDS

pytestmark = pytest.mark.llm_eval

AS_AT = date(2026, 7, 28)


@pytest.fixture
async def eval_session():
    if not settings.anthropic_api_key:
        pytest.skip("anthropic_api_key not configured")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            act = (
                await session.execute(select(Act).where(Act.slug == "act-2010-042"))
            ).scalar_one_or_none()
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("corpus store not reachable")
        if act is None:
            pytest.skip("corpus not ingested")
        yield session
    await engine.dispose()


def _assert_thresholds(stats: dict) -> None:
    failures = []
    print(f"\n{'rule':45} {'P':>6} {'R':>6} {'yellow':>7}")
    for rule_id, counts in sorted(stats.items()):
        tp, fp, misses, yellows = counts["tp"], counts["fp"], counts["miss"], counts["yellow"]
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + misses) if tp + misses else 1.0
        min_p, min_r = THRESHOLDS.get(rule_id, THRESHOLDS["default"])
        print(f"{rule_id:45} {precision:6.2f} {recall:6.2f} {yellows:7d}")
        if precision < min_p:
            failures.append(f"{rule_id} precision {precision:.2f} < {min_p}")
        if recall < min_r:
            failures.append(f"{rule_id} recall {recall:.2f} < {min_r}")
    assert not failures, "; ".join(failures)


async def _score_family(session, runner, rules, cases):
    judge = make_judge()
    rule_ids = {r.rule_id for r in rules}
    stats: dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "miss": 0, "yellow": 0})
    for case in cases:
        findings = await runner(judge, session, DocumentInput(kind="text", text=case.text), AS_AT)
        for finding in findings:
            if finding.rule_id not in rule_ids or finding.verdict == "skipped":
                continue
            is_target = finding.rule_id == case.rule_id and case.expected == "red"
            expected = "red" if is_target else "green"
            counts = stats[finding.rule_id]
            if finding.verdict == "yellow":
                counts["yellow"] += 1
                if expected == "red":
                    counts["miss"] += 1
            elif finding.verdict == "red" and expected == "red":
                counts["tp"] += 1
            elif finding.verdict == "red" and expected == "green":
                counts["fp"] += 1
            elif finding.verdict == "green" and expected == "red":
                counts["miss"] += 1
    return stats


async def test_prohibited_golden(eval_session):
    stats = await _score_family(eval_session, run_prohibited, PROHIBITED_RULES, PROHIBITED_CASES)
    _assert_thresholds(stats)


async def test_mandatory_golden(eval_session):
    stats = await _score_family(eval_session, run_mandatory, MANDATORY_RULES, MANDATORY_CASES)
    _assert_thresholds(stats)


async def test_fields_golden(eval_session):
    judge = make_judge()
    wrong = []
    for case in FIELD_CASES:
        lease = ClauseLeaseInput.model_validate(case.lease)
        found = await run_fields(judge, DocumentInput(kind="text", text=case.text), lease)
        got = {d.field for d in found}
        if got != case.expected:
            wrong.append(f"{case.case_id}: expected {case.expected}, got {got}")
    assert not wrong, "; ".join(wrong)


SEEDED = (
    "RESIDENTIAL TENANCY AGREEMENT. The rent is $560 per week payable weekly. "
    "The tenant must have all carpets professionally steam cleaned at the "
    "conclusion of the tenancy and provide a receipt to the landlord. "
    "The tenant must keep the premises reasonably clean."
)


async def test_pdf_smoke_text_layer(eval_session):
    doc = document_input("pdf", make_text_pdf(SEEDED))
    assert doc.kind == "text"
    findings = await run_prohibited(make_judge(), eval_session, doc, AS_AT)
    carpet = next(f for f in findings if f.rule_id == "nsw.clause.carpet_cleaning")
    assert carpet.verdict == "red"


async def test_pdf_smoke_scanned(eval_session):
    doc = document_input("pdf", make_scanned_pdf(SEEDED))
    assert doc.kind == "pdf"
    findings = await run_prohibited(make_judge(), eval_session, doc, AS_AT)
    carpet = next(f for f in findings if f.rule_id == "nsw.clause.carpet_cleaning")
    assert carpet.verdict == "red"
```

Also add a CI-safe structural check appended to `tests/test_clause_rules.py` (runs in the normal suite, no marker):

```python
def test_golden_covers_every_clause_rule():
    from tests.golden.clauses import MANDATORY_CASES, PROHIBITED_CASES

    covered = {c.rule_id for c in PROHIBITED_CASES}
    assert covered == {r.rule_id for r in PROHIBITED_RULES}
    mandatory_covered = {c.rule_id for c in MANDATORY_CASES}
    assert mandatory_covered == {r.rule_id for r in MANDATORY_RULES}
    for case in PROHIBITED_CASES + MANDATORY_CASES:
        assert case.expected in ("red", "green")
```

This test forces the Task 4 rule additions and their golden cases to land together — it fails until `MANDATORY_CASES` (and any extra prohibited blocks) are written.

- [ ] **Step 4: Run the CI-safe layer**

Run: `uv run pytest tests/test_clause_rules.py -v`
Expected: PASS only once every rule has golden cases; fix the data until it does. Then `uv run pytest` — the eval tests must show as deselected.

- [ ] **Step 5: Run the real eval**

Run: `uv run pytest -m llm_eval -v -s`
Expected: PASS with the per-rule P/R table printed. Budget roughly US$2-3. If a rule misses a threshold, tune its `question` wording (Task 4 file) or the prompt, rerun, and note the iteration in the commit message. Prompt or rule changes here are why `ENGINE_VERSION` already moved to 1.1.0 this milestone.

The same harness is the model-comparison gate promised by the spec — a
candidate model is evaluated with nothing but an env override:

```bash
CLAUSE_AUDIT_MODEL=claude-sonnet-5 uv run pytest -m llm_eval -v -s
```

(No switch this milestone; record the command in the report so the later
downgrade decision has its procedure.)

- [ ] **Step 6: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add the clause audit golden sets and eval harness" && git push origin main
```

---

## Acceptance (manual, after Task 8)

```bash
ANTHROPIC_API_KEY=... uv run uvicorn app.main:app --port 8100
curl -s -X POST http://localhost:8100/v1/clause-audits \
  -H "X-API-Key: <dev key>" \
  -F 'payload={"jurisdiction": "NSW", "client_ref": "demo-1"}' \
  -F 'text=AGREEMENT. The tenant must have the carpet professionally cleaned at the end of the tenancy.'
# poll until succeeded:
curl -s http://localhost:8100/v1/clause-audits/<id> -H "X-API-Key: <dev key>"
```

Expected: `nsw.clause.carpet_cleaning` red with a quote and an s 19 citation; document column NULL afterwards (`select document from clause_audit_jobs` returns NULL).
