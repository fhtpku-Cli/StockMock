"""全量账本只读核对与追加边界分页。"""

import csv
import io
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import Response

from app.analysis_models import CashStatement, CurrencyStatement, LedgerPage, LedgerQuery
from app.models import Currency, DomainError, Entry, State
from app.plan_exports import safe_cell

BEIJING = ZoneInfo("Asia/Shanghai")


def entry_day(entry: Entry) -> date:
    """以确认时间筛选北京时间自然日。"""
    return entry.created_at.astimezone(BEIJING).date()


def matches(entry: Entry, query: LedgerQuery) -> bool:
    """筛选发生在分页之前。"""
    return (
        (query.start_date is None or entry_day(entry) >= query.start_date)
        and (query.end_date is None or entry_day(entry) <= query.end_date)
        and (query.currency is None or entry.currency == query.currency)
        and (query.kind is None or entry.kind == query.kind)
    )


def ledger_page(state: State, query: LedgerQuery) -> LedgerPage:
    """保留读取第一页时的追加边界；新项留给刷新。"""
    boundary = len(state.ledger) if query.boundary is None else query.boundary
    if boundary > len(state.ledger):
        raise DomainError("LEDGER_CHANGED", "本轮流水已变化，请刷新第一页", 409)
    rows = [row for row in reversed(state.ledger[:boundary]) if matches(row, query)]
    return LedgerPage(
        items=rows[query.offset : query.offset + query.limit],
        total=len(rows),
        offset=query.offset,
        limit=query.limit,
        boundary=boundary,
        round_id=state.id,
    )


def statement(state: State, query: LedgerQuery) -> CashStatement:
    """始终按当次完整轮次核对；列表boundary和类型不截断全类型汇总。"""
    result: list[CurrencyStatement] = []
    currencies: tuple[Currency, ...] = ("CNY", "USD", "HKD")
    for currency in currencies:
        if query.currency is not None and query.currency != currency:
            continue
        initial = Decimal(1000000) if currency == "CNY" else Decimal(0)
        rows = [row for row in state.ledger if row.currency == currency]
        before = [row for row in rows if query.start_date is not None and entry_day(row) < query.start_date]
        period = [
            row
            for row in rows
            if (query.start_date is None or entry_day(row) >= query.start_date)
            and (query.end_date is None or entry_day(row) <= query.end_date)
        ]

        def net(entries: list[Entry]) -> Decimal:
            return sum((row.amount for row in entries), Decimal(0))

        def incoming(entries: list[Entry]) -> Decimal:
            return net([row for row in entries if row.amount > 0])

        def outgoing(entries: list[Entry]) -> Decimal:
            return -net([row for row in entries if row.amount < 0])

        calculated = initial + net(rows)
        opening = initial + net(before)
        result.append(
            CurrencyStatement(
                currency=currency,
                initial_cash=initial,
                total_inflow=incoming(rows),
                total_outflow=outgoing(rows),
                calculated_balance=calculated,
                ledger_balance=state.cash[currency],
                difference=state.cash[currency] - calculated,
                opening_balance=opening,
                period_inflow=incoming(period),
                period_outflow=outgoing(period),
                period_net_change=net(period),
                closing_balance=opening + net(period),
                selected_net_change=net([row for row in period if query.kind is None or row.kind == query.kind]),
            )
        )
    return CashStatement(
        round_id=state.id,
        start_date=query.start_date,
        end_date=query.end_date,
        boundary=len(state.ledger),
        currencies=result,
    )


def filtered_csv(state: State, query: LedgerQuery) -> Response:
    """一次完整读取导出所有匹配记录，不受分页边界影响。"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "轮次",
            "开始日",
            "结束日",
            "筛选币种",
            "筛选类型",
            "编号",
            "类型",
            "币种",
            "金额",
            "确认时间",
            "委托编号",
            "说明",
        ]
    )
    for row in reversed(state.ledger):
        if matches(row, query):
            writer.writerow(
                [
                    state.id,
                    query.start_date or "",
                    query.end_date or "",
                    query.currency or "全部",
                    safe_cell(query.kind or "全部"),
                    safe_cell(row.id),
                    safe_cell(row.kind),
                    row.currency,
                    row.amount,
                    row.created_at.isoformat(),
                    safe_cell(row.order_id or ""),
                    safe_cell(row.note),
                ]
            )
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="filtered-ledger.csv"'},
    )
