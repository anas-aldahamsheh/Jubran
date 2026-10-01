"""What the assistant costs: tokens per turn and an estimated price, for the admin.

Prices change; only models whose price is listed here get an estimated cost (the
others still show their tokens). Amounts are US dollars per million tokens:
(input, cached input, output). Output includes reasoning ("thinking") tokens.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.infrastructure.db.models import AssistantUsageModel

PRICES_PER_MILLION_USD = {
    "gpt-6-luna": (0.10, 0.01, 0.50),  # developers.openai.com, September 2026
}
KEEP_DAYS = 90


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    def add(self, *, input_tokens: int = 0, cached_input_tokens: int = 0, output_tokens: int = 0,
            reasoning_tokens: int = 0) -> None:
        self.calls += 1
        self.input_tokens += int(input_tokens or 0)
        self.cached_input_tokens += int(cached_input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)
        self.reasoning_tokens += int(reasoning_tokens or 0)


def estimated_cost_microusd(model_id: str, usage: Usage) -> Optional[int]:
    price = PRICES_PER_MILLION_USD.get((model_id or "").strip().lower())
    if price is None:
        return None
    input_price, cached_price, output_price = price
    uncached = max(usage.input_tokens - usage.cached_input_tokens, 0)
    dollars = (uncached * input_price + usage.cached_input_tokens * cached_price
               + usage.output_tokens * output_price) / 1_000_000
    return round(dollars * 1_000_000)


async def record_usage(db: AsyncSession, *, customer_session_id: Optional[str], channel: str, provider: str,
                       model_id: str, usage: Usage, succeeded: bool) -> None:
    """Store one turn's usage (never lets a bookkeeping problem break the guest's turn)."""
    if usage.calls == 0:
        return
    try:
        db.add(AssistantUsageModel(
            customer_session_id=customer_session_id, channel=channel, provider=provider, model_id=model_id,
            model_calls=usage.calls, input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens, output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens, cost_microusd=estimated_cost_microusd(model_id, usage),
            succeeded=succeeded,
        ))
        await db.commit()
    except Exception:  # pragma: no cover - bookkeeping only
        await db.rollback()


async def usage_summary(db: AsyncSession, days: int = 7) -> Dict[str, Any]:
    """Totals and averages per model for the last ``days`` days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(
        select(AssistantUsageModel.provider, AssistantUsageModel.model_id,
               func.count(AssistantUsageModel.id), func.sum(AssistantUsageModel.model_calls),
               func.sum(AssistantUsageModel.input_tokens), func.sum(AssistantUsageModel.cached_input_tokens),
               func.sum(AssistantUsageModel.output_tokens), func.sum(AssistantUsageModel.reasoning_tokens),
               func.sum(AssistantUsageModel.cost_microusd), func.count(AssistantUsageModel.cost_microusd),
               func.count(func.distinct(AssistantUsageModel.customer_session_id)))
        .where(AssistantUsageModel.created_at >= since)
        .group_by(AssistantUsageModel.provider, AssistantUsageModel.model_id)
    )).all()
    models = []
    for provider, model_id, turns, calls, tokens_in, cached, tokens_out, reasoning, cost, priced, guests in rows:
        cost_usd = (cost or 0) / 1_000_000 if priced else None
        models.append({
            "provider": provider, "model_id": model_id, "turns": turns, "guests": guests,
            "model_calls": int(calls or 0), "input_tokens": int(tokens_in or 0),
            "cached_input_tokens": int(cached or 0), "output_tokens": int(tokens_out or 0),
            "reasoning_tokens": int(reasoning or 0),
            "cost_usd": round(cost_usd, 4) if cost_usd is not None else None,
            "cost_per_turn_usd": round(cost_usd / turns, 5) if cost_usd is not None and turns else None,
            "calls_per_turn": round((calls or 0) / turns, 2) if turns else None,
        })
    return {"days": days, "models": models}


async def purge_old_usage(db: AsyncSession) -> None:
    await db.execute(delete(AssistantUsageModel).where(
        AssistantUsageModel.created_at < datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)))
    await db.commit()
