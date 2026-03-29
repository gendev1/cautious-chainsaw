"""
app/models/access_scope.py — Structured access scope for retrieval filtering.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field


class AccessScope(BaseModel):
    """
    Defines the visibility boundary for a single request.

    The platform computes this from the actor's role, team assignments,
    and any explicit sharing rules. The sidecar treats it as immutable
    truth for the duration of the request.
    """

    tenant_id: str = ""
    actor_id: str = ""
    actor_type: str = ""
    request_id: str = ""
    conversation_id: str | None = None

    visibility_mode: Literal["full_tenant", "scoped"] = Field(
        default="scoped",
        description=(
            "'full_tenant' — actor can see all data in the tenant. "
            "'scoped' — actor can only see the resource sets listed below."
        ),
    )

    household_ids: list[str] = Field(default_factory=list)
    client_ids: list[str] = Field(default_factory=list)
    account_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    advisor_ids: list[str] = Field(default_factory=list)

    def fingerprint(self) -> str:
        """Stable hash of the scope for use in cache keys."""
        raw = self.model_dump_json()
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def allows_household(self, household_id: str) -> bool:
        if self.visibility_mode == "full_tenant":
            return True
        return household_id in self.household_ids

    def allows_client(self, client_id: str) -> bool:
        if self.visibility_mode == "full_tenant":
            return True
        return client_id in self.client_ids

    def allows_account(self, account_id: str) -> bool:
        if self.visibility_mode == "full_tenant":
            return True
        return account_id in self.account_ids

    def allows_document(self, document_id: str) -> bool:
        if self.visibility_mode == "full_tenant":
            return True
        return document_id in self.document_ids

    def to_vector_filter(self, tenant_id: str) -> dict:
        """
        Build a metadata filter dict for vector store queries.
        Always includes tenant_id for hard isolation.
        Adds resource-level filters when scope is not full_tenant.
        """
        base: dict = {"tenant_id": tenant_id}

        if self.visibility_mode == "full_tenant":
            return base

        allowed: dict[str, list[str]] = {}
        if self.household_ids:
            allowed["household_id"] = self.household_ids
        if self.client_ids:
            allowed["client_id"] = self.client_ids
        if self.account_ids:
            allowed["account_id"] = self.account_ids
        if self.advisor_ids:
            allowed["advisor_id"] = self.advisor_ids

        if allowed:
            base["_or"] = allowed

        return base
