from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from api.deps import require_admin
from db.models import QueryLog, User
from db.session import get_db

router = APIRouter(prefix="/admin/metrics", tags=["admin"])


class ModelUsage(BaseModel):
    model: str
    count: int


class DailyPoint(BaseModel):
    date: str
    total: int
    errors: int


class FeedbackEntry(BaseModel):
    log_id: int
    user_name: str | None
    user_email: str | None
    rating: int
    question: str
    feedback_text: str | None
    feedback_at: str | None


class MetricsResponse(BaseModel):
    range_days: int
    total_queries: int
    error_rate: float
    success_rate: float
    avg_latency_ms: float | None
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    feedback_total: int
    feedback_positive: int
    feedback_negative: int
    top_models: list[ModelUsage]
    daily: list[DailyPoint]
    feedbacks: list[FeedbackEntry]


def _percentile(sorted_values: list[int], pct: float) -> int | None:
    if not sorted_values:
        return None
    k = max(0, min(len(sorted_values) - 1, int(round((pct / 100.0) * (len(sorted_values) - 1)))))
    return sorted_values[k]


def _build_daily_series(daily_rows, since_date: date, end_date: date) -> list[DailyPoint]:
    # Mapeia dia -> (total, erros). func.date() retorna date no Postgres.
    day_map: dict[str, tuple[int, int]] = {}
    for d, t, e in daily_rows:
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        day_map[key] = (int(t or 0), int(e or 0))

    points: list[DailyPoint] = []
    cursor = since_date
    while cursor <= end_date:
        key = cursor.isoformat()
        total, errors = day_map.get(key, (0, 0))
        points.append(DailyPoint(date=key, total=total, errors=errors))
        cursor += timedelta(days=1)
    return points


@router.get("", response_model=MetricsResponse)
def get_metrics(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    since = now - timedelta(days=days)

    base = db.query(QueryLog).filter(QueryLog.created_at >= since)

    total = base.count()
    errors = base.filter(QueryLog.success.is_(False)).count()
    error_rate = (errors / total) if total else 0.0

    latencies = [
        row[0]
        for row in (
            db.query(QueryLog.latency_ms)
            .filter(QueryLog.created_at >= since, QueryLog.latency_ms.isnot(None))
            .all()
        )
    ]
    latencies.sort()
    avg = (sum(latencies) / len(latencies)) if latencies else None

    pos = base.filter(QueryLog.rating == 1).count()
    neg = base.filter(QueryLog.rating == -1).count()

    model_rows = (
        db.query(QueryLog.model_used, func.count(QueryLog.id))
        .filter(QueryLog.created_at >= since, QueryLog.model_used.isnot(None))
        .group_by(QueryLog.model_used)
        .order_by(func.count(QueryLog.id).desc())
        .limit(5)
        .all()
    )
    top_models = [ModelUsage(model=m or "(desconhecido)", count=c) for m, c in model_rows]

    error_expr = case((QueryLog.success.is_(False), 1), else_=0)
    daily_rows = (
        db.query(
            func.date(QueryLog.created_at).label("day"),
            func.count(QueryLog.id).label("total"),
            func.sum(error_expr).label("errors"),
        )
        .filter(QueryLog.created_at >= since)
        .group_by("day")
        .order_by("day")
        .all()
    )
    daily = _build_daily_series(daily_rows, since.date(), now.date())

    feedback_rows = (
        db.query(QueryLog, User)
        .outerjoin(User, User.id == QueryLog.user_id)
        .filter(
            QueryLog.created_at >= since,
            QueryLog.rating.isnot(None),
        )
        .order_by(QueryLog.feedback_at.desc().nullslast(), QueryLog.created_at.desc())
        .limit(50)
        .all()
    )
    feedbacks = [
        FeedbackEntry(
            log_id=log.id,
            user_name=(user.full_name if user else None),
            user_email=(user.email if user else None),
            rating=log.rating,
            question=(log.question or "")[:300],
            feedback_text=log.feedback_text,
            feedback_at=(log.feedback_at.isoformat() if log.feedback_at else (log.created_at.isoformat() if log.created_at else None)),
        )
        for log, user in feedback_rows
    ]

    return MetricsResponse(
        range_days=days,
        total_queries=total,
        error_rate=round(error_rate, 4),
        success_rate=round(1.0 - error_rate, 4),
        avg_latency_ms=round(avg, 1) if avg is not None else None,
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        feedback_total=pos + neg,
        feedback_positive=pos,
        feedback_negative=neg,
        top_models=top_models,
        daily=daily,
        feedbacks=feedbacks,
    )
