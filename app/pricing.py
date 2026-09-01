"""FREE-ONLY pricing guard.

Enforces FREE_ONLY_MODE, tracks when provider pricing was last verified, and
produces the stale-pricing warnings the UI must show. Never purchases credits,
subscribes, or enables paid fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import Database


class PricingError(Exception):
    def __init__(self, message: str, code: str = "pricing"):
        super().__init__(message)
        self.message = message
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def get_provider_meta(db: Database, provider: str, model: str | None = None) -> dict[str, Any] | None:
    rows = db.provider_metadata()
    for row in rows:
        if row["provider"] == provider and (not model or row["model"] == model):
            return dict(row)
    return None


def is_free(meta: dict[str, Any] | None) -> bool:
    return bool(meta and meta.get("pricing_status") == "free" and meta.get("enabled"))


def pricing_stale(meta: dict[str, Any] | None, stale_days: int) -> bool:
    if not meta:
        return True
    verified = _parse_iso(meta.get("last_verified"))
    if verified is None:
        return True
    return verified < _now() - timedelta(days=stale_days)


def check_free_only(
    db: Database,
    provider: str,
    model: str | None,
    free_only_mode: bool,
) -> None:
    """Raise PricingError when the provider is not verified free.

    Called before submission so no paid/unknown provider can ever run.
    """
    if not free_only_mode:
        # FREE_ONLY_MODE disabled by operator explicitly. We still refuse
        # providers we know nothing about.
        meta = get_provider_meta(db, provider, model)
        if meta is None:
            raise PricingError(
                f"provider '{provider}/{model}' has no pricing metadata; refusing "
                "rather than risking paid generation",
                code="unknown_pricing",
            )
        return
    meta = get_provider_meta(db, provider, model)
    if not is_free(meta):
        if meta is None:
            raise PricingError(
                f"provider '{provider}/{model}' is not verified as free",
                code="unknown_pricing",
            )
        raise PricingError(
            f"provider '{provider}/{model}' is not marked free "
            f"(status={meta.get('pricing_status')!r}); FREE_ONLY_MODE is on",
            code="not_free",
        )


def large_batch_requires_ack(
    db: Database,
    jobs_count: int,
    large_threshold: int,
    stale_days: int,
) -> bool:
    """True when a large batch + stale pricing requires an acknowledgement."""
    if jobs_count <= large_threshold:
        return False
    metas = db.provider_metadata()
    any_stale = any(
        pricing_stale(dict(m), stale_days)
        for m in metas
        if dict(m).get("pricing_status") == "free"
    )
    return any_stale


def stale_warning_message(db: Database, stale_days: int) -> str | None:
    metas = db.provider_metadata()
    stale = [
        m for m in metas
        if dict(m).get("pricing_status") == "free"
        and pricing_stale(dict(m), stale_days)
    ]
    if not stale:
        return None
    names = ", ".join(sorted({m["provider"] for m in stale}))
    return (
        f"Free pricing for {names} was last verified more than {stale_days} days "
        "ago. Free pricing can change. Verify provider pricing before very large "
        "batches."
    )
