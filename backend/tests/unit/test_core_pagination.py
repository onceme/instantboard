import pytest
from sqlalchemy import select

from app.core.exceptions import ValidationError
from app.core.pagination import apply_sort
from app.models.category import Category


class TestApplySort:
    def test_none_sort_by_returns_query_unchanged(self):
        stmt = select(Category)
        assert apply_sort(stmt, None, {"name"}, Category) is stmt

    def test_asc_ordering(self):
        stmt = apply_sort(select(Category), "name", {"name"}, Category, "asc")
        assert "ORDER BY categories.name ASC" in str(stmt)

    def test_desc_ordering_is_default(self):
        stmt = apply_sort(select(Category), "created_at", {"created_at"}, Category)
        assert "ORDER BY categories.created_at DESC" in str(stmt)

    def test_desc_ordering_explicit(self):
        stmt = apply_sort(select(Category), "type", {"type"}, Category, "desc")
        assert "ORDER BY categories.type DESC" in str(stmt)

    def test_sort_by_outside_whitelist_raises_400(self):
        with pytest.raises(ValidationError) as excinfo:
            apply_sort(select(Category), "slug", {"name", "created_at"}, Category)
        error = excinfo.value
        assert error.status_code == 400
        assert error.error_code == "VALIDATION_ERROR"
        assert error.error_details[0]["field"] == "sort_by"
        assert "name" in error.error_details[0]["message"]

    def test_invalid_sort_order_raises_400(self):
        with pytest.raises(ValidationError) as excinfo:
            apply_sort(select(Category), "name", {"name"}, Category, "sideways")
        error = excinfo.value
        assert error.status_code == 400
        assert error.error_details[0]["field"] == "sort_order"

    def test_sql_injection_string_rejected(self):
        # Never reaches getattr(): the whitelist check rejects anything that is not
        # an explicitly allowed column name.
        with pytest.raises(ValidationError):
            apply_sort(select(Category), "name); DROP TABLE categories; --", {"name"}, Category)

    def test_case_mismatch_rejected(self):
        with pytest.raises(ValidationError):
            apply_sort(select(Category), "NAME", {"name"}, Category)
