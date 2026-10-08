"""The only code allowed to change stock.

Every posting runs inside the caller's transaction:
  1. make sure a stock_balances row exists for each (store, material),
  2. lock those rows with SELECT … FOR UPDATE in a fixed order (no deadlocks between concurrent postings),
  3. apply each movement with weighted-average costing, append an immutable ledger row, update the balance.

Weighted average:  avg = (cur_qty × cur_avg + in_qty × in_rate) / (cur_qty + in_qty)
Outward movements leave at the current average unless an explicit rate is given (used for reversals).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Material, StockBalance, StockLedger
from app.services.common import now_utc, q3, q4

ZERO = Decimal("0")


@dataclass
class Movement:
    store_id: uuid.UUID
    material_id: uuid.UUID
    qty: Decimal  # always positive
    inward: bool
    txn_type: str
    ref_type: str
    ref_id: uuid.UUID
    ref_no: str | None
    txn_date: date
    rate: Decimal | None = None  # required for inward; optional override for outward


@dataclass
class Posted:
    movement: Movement
    rate: Decimal
    value: Decimal  # absolute value moved
    balance_qty: Decimal


async def _lock_balances(db: AsyncSession, keys: list[tuple[uuid.UUID, uuid.UUID]]) -> dict:
    await db.execute(
        pg_insert(StockBalance)
        .values([{"store_id": s, "material_id": m, "qty": 0, "value": 0, "avg_rate": 0} for s, m in keys])
        .on_conflict_do_nothing()
    )
    balances = {}
    for store_id, material_id in sorted(keys, key=lambda k: (str(k[0]), str(k[1]))):
        bal = (
            await db.execute(
                select(StockBalance)
                .where(StockBalance.store_id == store_id, StockBalance.material_id == material_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        balances[(store_id, material_id)] = bal
    return balances


async def post_movements(db: AsyncSession, movements: list[Movement], user_id: uuid.UUID | None) -> list[Posted]:
    if not movements:
        return []
    keys = list({(m.store_id, m.material_id) for m in movements})
    balances = await _lock_balances(db, keys)
    results: list[Posted] = []
    stamp = now_utc()

    for m in movements:
        if m.qty <= 0:
            raise AppError("INVALID_QTY", "Quantity must be greater than zero")
        bal = balances[(m.store_id, m.material_id)]
        qty = q3(m.qty)
        cur_qty, cur_value, cur_avg = Decimal(bal.qty), Decimal(bal.value), Decimal(bal.avg_rate)

        if m.inward:
            if m.rate is None or m.rate < 0:
                raise AppError("INVALID_RATE", "Inward movement requires a non-negative rate")
            rate = q4(m.rate)
            value = q4(qty * rate)
            new_qty = cur_qty + qty
            new_value = cur_value + value
            new_avg = q4(new_value / new_qty) if new_qty > 0 else cur_avg
        else:
            if qty > cur_qty:
                material = await db.get(Material, m.material_id)
                raise AppError(
                    "INSUFFICIENT_STOCK",
                    f"Only {cur_qty.normalize():f} available for {material.name if material else 'material'}; "
                    f"{qty.normalize():f} requested",
                    409,
                    {
                        "store_id": str(m.store_id),
                        "material_id": str(m.material_id),
                        "material": material.name if material else None,
                        "available": float(cur_qty),
                        "requested": float(qty),
                    },
                )
            rate = q4(m.rate) if m.rate is not None else cur_avg
            value = q4(qty * rate)
            new_qty = cur_qty - qty
            new_value = cur_value - value
            if new_qty == 0 or new_value < 0:
                # Rounding residue (or a reversal at a historic rate) must not leave orphan value.
                new_value = ZERO if new_qty == 0 else q4(new_qty * cur_avg)
            new_avg = q4(new_value / new_qty) if new_qty > 0 else cur_avg

        bal.qty, bal.value, bal.avg_rate, bal.last_movement_at = new_qty, new_value, new_avg, stamp
        db.add(
            StockLedger(
                store_id=m.store_id,
                material_id=m.material_id,
                txn_date=m.txn_date,
                txn_type=m.txn_type,
                ref_type=m.ref_type,
                ref_id=m.ref_id,
                ref_no=m.ref_no,
                qty_in=qty if m.inward else ZERO,
                qty_out=ZERO if m.inward else qty,
                rate=rate,
                value=value if m.inward else -value,
                balance_qty=new_qty,
                balance_value=new_value,
                avg_rate_after=new_avg,
                created_by_id=user_id,
            )
        )
        results.append(Posted(m, rate, value, new_qty))

    await db.flush()
    return results


async def available(db: AsyncSession, store_id: uuid.UUID, material_ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    rows = await db.execute(
        select(StockBalance.material_id, StockBalance.qty).where(
            StockBalance.store_id == store_id, StockBalance.material_id.in_(material_ids)
        )
    )
    out = {mid: ZERO for mid in material_ids}
    out.update({mid: Decimal(q) for mid, q in rows})
    return out
