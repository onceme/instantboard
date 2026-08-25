from typing import Any

from sqlalchemy import Select

from app.core.exceptions import ValidationError


def apply_sort(
    query: Select,
    sort_by: str | None,
    allowed: set[str],
    model: type[Any],
    sort_order: str = "desc",
) -> Select:
    """Apply a whitelisted ORDER BY to a select statement.

    When `sort_by` is None the query is returned unchanged so the caller keeps its
    own default ordering. Any `sort_by` outside `allowed` raises 400
    VALIDATION_ERROR; the column is resolved via `getattr(model, sort_by)` only
    after the whitelist check, so user input can never inject arbitrary SQL
    identifiers.
    """
    if sort_by is None:
        return query

    if sort_by not in allowed:
        raise ValidationError(
            message=f"Invalid sort_by value '{sort_by}'. Allowed fields: {', '.join(sorted(allowed))}",
            details=[
                {
                    "field": "sort_by",
                    "message": f"Must be one of: {', '.join(sorted(allowed))}",
                }
            ],
        )

    direction = sort_order.lower()
    if direction not in ("asc", "desc"):
        raise ValidationError(
            message=f"Invalid sort_order value '{sort_order}'. Must be 'asc' or 'desc'",
            details=[{"field": "sort_order", "message": "Must be 'asc' or 'desc'"}],
        )

    column = getattr(model, sort_by)
    return query.order_by(column.asc() if direction == "asc" else column.desc())
