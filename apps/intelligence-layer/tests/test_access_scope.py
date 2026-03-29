"""Tests for AccessScope model and scope enforcement."""
from __future__ import annotations

from app.models.access_scope import AccessScope


def test_full_tenant_allows_any_household() -> None:
    """T6: full_tenant mode allows access to any household."""
    scope = AccessScope(visibility_mode="full_tenant")
    assert scope.allows_household("any_household") is True


def test_scoped_denies_unlisted_household() -> None:
    """T7: scoped mode denies access to unlisted household."""
    scope = AccessScope(visibility_mode="scoped", household_ids=["h1"])
    assert scope.allows_household("h2") is False


def test_scoped_allows_listed_household() -> None:
    """T7b: scoped mode allows access to listed household."""
    scope = AccessScope(visibility_mode="scoped", household_ids=["h1", "h2"])
    assert scope.allows_household("h1") is True


def test_full_tenant_allows_any_client() -> None:
    """full_tenant allows any client."""
    scope = AccessScope(visibility_mode="full_tenant")
    assert scope.allows_client("c_any") is True


def test_scoped_denies_unlisted_client() -> None:
    """scoped denies unlisted client."""
    scope = AccessScope(visibility_mode="scoped", client_ids=["c1"])
    assert scope.allows_client("c2") is False


def test_to_vector_filter_full_tenant() -> None:
    """T8a: full_tenant filter includes only tenant_id."""
    scope = AccessScope(visibility_mode="full_tenant")
    f = scope.to_vector_filter("t_1")
    assert f == {"tenant_id": "t_1"}


def test_to_vector_filter_scoped() -> None:
    """T8b: scoped filter includes tenant_id and OR filter."""
    scope = AccessScope(
        visibility_mode="scoped",
        household_ids=["h1", "h2"],
        client_ids=["c1"],
    )
    f = scope.to_vector_filter("t_1")
    assert f["tenant_id"] == "t_1"
    assert "_or" in f
    assert f["_or"]["household_id"] == ["h1", "h2"]
    assert f["_or"]["client_id"] == ["c1"]


def test_allows_document() -> None:
    """scoped allows listed document."""
    scope = AccessScope(visibility_mode="scoped", document_ids=["d1"])
    assert scope.allows_document("d1") is True
    assert scope.allows_document("d2") is False


def test_allows_account() -> None:
    """scoped allows listed account."""
    scope = AccessScope(visibility_mode="scoped", account_ids=["a1"])
    assert scope.allows_account("a1") is True
    assert scope.allows_account("a2") is False
