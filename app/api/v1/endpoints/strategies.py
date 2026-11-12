"""Strategy catalogue, strategies and their immutable versions."""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.backtesting.engine import ENGINE_VERSION
from app.backtesting.strategies import (
    STRATEGY_REGISTRY,
    InvalidStrategyParametersError,
    UnknownStrategyTypeError,
    get_strategy_definition,
    validate_parameters,
)
from app.core.errors import NotFoundError, UnprocessableError
from app.db.session import get_db
from app.models.identity import User
from app.models.research import Strategy, StrategyVersion
from app.schemas.strategy import (
    StrategyCreate,
    StrategyRead,
    StrategyTypeRead,
    StrategyUpdate,
    StrategyVersionCreate,
    StrategyVersionRead,
)

router = APIRouter()


def _validated(strategy_type: str, parameters: dict) -> dict:
    try:
        return validate_parameters(strategy_type, parameters)
    except (UnknownStrategyTypeError, InvalidStrategyParametersError) as exc:
        raise UnprocessableError(str(exc)) from exc


async def _get_owned_strategy(
    db: AsyncSession, user_id: uuid.UUID, strategy_id: uuid.UUID, *, for_update: bool = False
) -> Strategy:
    stmt = select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    strategy = (await db.execute(stmt)).scalar_one_or_none()
    if strategy is None:
        raise NotFoundError("Strategy not found")
    return strategy


async def _latest_versions(
    db: AsyncSession, strategy_ids: list[uuid.UUID]
) -> dict[uuid.UUID, StrategyVersion]:
    if not strategy_ids:
        return {}
    newest = (
        select(
            StrategyVersion.strategy_id,
            func.max(StrategyVersion.version).label("version"),
        )
        .where(StrategyVersion.strategy_id.in_(strategy_ids))
        .group_by(StrategyVersion.strategy_id)
        .subquery()
    )
    stmt = select(StrategyVersion).join(
        newest,
        and_(
            StrategyVersion.strategy_id == newest.c.strategy_id,
            StrategyVersion.version == newest.c.version,
        ),
    )
    return {version.strategy_id: version for version in (await db.execute(stmt)).scalars()}


def _to_read(strategy: Strategy, latest: StrategyVersion | None) -> StrategyRead:
    read = StrategyRead.model_validate(strategy)
    if latest is not None:
        read.latest_version = StrategyVersionRead.model_validate(latest)
    return read


@router.get("/types", response_model=list[StrategyTypeRead])
async def list_strategy_types(_: User = Depends(get_current_user)):
    """Built-in strategy types with their parameter schemas and defaults."""
    return [
        StrategyTypeRead(
            type=definition.strategy_type,
            name=definition.name,
            description=definition.description,
            default_parameters=definition.default_parameters(),
            parameter_schema=definition.parameter_schema(),
        )
        for definition in STRATEGY_REGISTRY.values()
    ]


@router.post("", response_model=StrategyRead, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    payload: StrategyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a strategy together with version 1 of its parameters."""
    parameters = _validated(payload.strategy_type, payload.parameters)
    strategy = Strategy(
        user_id=current_user.id,
        name=payload.name,
        strategy_type=get_strategy_definition(payload.strategy_type).strategy_type,
        description=payload.description,
    )
    db.add(strategy)
    await db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version=1, parameters=parameters, code_version=ENGINE_VERSION
    )
    db.add(version)
    await db.commit()
    await db.refresh(strategy)
    return _to_read(strategy, version)


@router.get("", response_model=list[StrategyRead])
async def list_strategies(
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Strategy)
        .where(Strategy.user_id == current_user.id)
        .order_by(Strategy.created_at.desc(), Strategy.id)
        .limit(limit)
        .offset(offset)
    )
    if not include_archived:
        stmt = stmt.where(Strategy.is_archived.is_(False))
    strategies = (await db.execute(stmt)).scalars().all()
    latest = await _latest_versions(db, [strategy.id for strategy in strategies])
    return [_to_read(strategy, latest.get(strategy.id)) for strategy in strategies]


@router.get("/{strategy_id}", response_model=StrategyRead)
async def get_strategy(
    strategy_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    strategy = await _get_owned_strategy(db, current_user.id, strategy_id)
    latest = await _latest_versions(db, [strategy.id])
    return _to_read(strategy, latest.get(strategy.id))


@router.patch("/{strategy_id}", response_model=StrategyRead)
async def update_strategy(
    strategy_id: uuid.UUID,
    payload: StrategyUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename, re-describe or archive a strategy. Parameters change only via new versions."""
    strategy = await _get_owned_strategy(db, current_user.id, strategy_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is None:
        changes.pop("name", None)
    if changes.get("is_archived") is None:
        changes.pop("is_archived", None)
    for field_name, value in changes.items():
        setattr(strategy, field_name, value)
    await db.commit()
    await db.refresh(strategy)
    latest = await _latest_versions(db, [strategy.id])
    return _to_read(strategy, latest.get(strategy.id))


@router.post(
    "/{strategy_id}/versions",
    response_model=StrategyVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_strategy_version(
    strategy_id: uuid.UUID,
    payload: StrategyVersionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Append a new immutable parameter version."""
    # Locking the strategy row serialises concurrent version numbering.
    strategy = await _get_owned_strategy(db, current_user.id, strategy_id, for_update=True)
    parameters = _validated(strategy.strategy_type, payload.parameters)
    current = await db.scalar(
        select(func.coalesce(func.max(StrategyVersion.version), 0)).where(
            StrategyVersion.strategy_id == strategy.id
        )
    )
    version = StrategyVersion(
        strategy_id=strategy.id,
        version=int(current or 0) + 1,
        parameters=parameters,
        code_version=ENGINE_VERSION,
    )
    db.add(version)
    await db.commit()
    return version


@router.get("/{strategy_id}/versions", response_model=list[StrategyVersionRead])
async def list_strategy_versions(
    strategy_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    strategy = await _get_owned_strategy(db, current_user.id, strategy_id)
    stmt = (
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id)
        .order_by(StrategyVersion.version.desc())
    )
    return (await db.execute(stmt)).scalars().all()
