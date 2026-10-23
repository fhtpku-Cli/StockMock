"""0.3.0 临时 SQLite 与真实 HTTP 行为证据。"""

import csv
import io
import os
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.asset_routes as assets
import app.trading_routes as trading
from app.analysis_models import CashStatement, LedgerPage, SnapshotRange
from app.api_models import ResetInput
from app.engine import Engine
from app.main import create_app
from app.models import Entry, Portfolio
from app.operations import reset
from app.personal_models import ValuationSnapshot
from app.snapshots import capture
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, sample


def evidence(name: str, value: str) -> None:
    target = os.getenv("STOCKMOCK_EVIDENCE")
    if target:
        _ = Path(target).mkdir(parents=True, exist_ok=True)
        _ = (Path(target) / name).write_text(value)


class Clock:
    current: datetime = NOW

    @classmethod
    def now(cls, _tz: tzinfo | None = None) -> datetime:
        return cls.current


def test_q04_http_buy_sell_and_analysis(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 临时真实账户和受控行情时钟。
    Clock.current = NOW
    monkeypatch.setattr(trading, "datetime", Clock)
    monkeypatch.setattr(assets, "datetime", Clock)
    engine.provider = ControlledProvider(price=Decimal(10))
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        body = {
            "request_id": str(uuid4()),
            "symbol": "600519.SS",
            "side": "buy",
            "quantity": 200,
            "order_type": "limit",
            "limit_price": "10",
        }
        assert client.post("/api/orders/preview", json=body).status_code == 200
        assert client.post("/api/orders", json=body).status_code == 200
        buy = engine.store.read().orders[-1]
        engine.process(buy.symbol, (sample(engine, buy, "10"),), NOW + timedelta(minutes=2))
        Clock.current = NOW + timedelta(days=1)
        engine.provider = ControlledProvider(price=Decimal(12), quote_time=Clock.current, history_day=NOW.date())
        sell_body = {**body, "request_id": str(uuid4()), "side": "sell", "quantity": 100, "limit_price": "12"}
        assert client.post("/api/orders", json=sell_body).status_code == 200
        sell = engine.store.read().orders[-1]
        engine.process(sell.symbol, (sample(engine, sell, "12"),), Clock.current + timedelta(minutes=2))
        engine.provider = ControlledProvider(price=Decimal(11), quote_time=Clock.current)
        before = engine.store.read()
        # When: HTTP读取资产、现金核对和完整CSV。
        response = client.get("/api/portfolio")
        account = Portfolio.model_validate_json(response.content)
        cash_response = client.get("/api/cash-statement")
        statement = CashStatement.model_validate_json(cash_response.content)
        exported = client.get("/api/ledger/export.csv")
        # Then: 已实现与浮动收益独立且总计一致，分析无写入。
        assert account.total_assets_cny == Decimal("1000298.40")
        assert account.pnl_cny == Decimal("298.40")
        assert account.positions[0].cost == Decimal("1000.50")
        assert account.positions[0].pnl == Decimal("99.50")
        assert before.orders[-1].realized_pnl == Decimal("198.90")
        assert statement.currencies[0].ledger_balance == Decimal("999198.40")
        assert statement.currencies[0].difference == 0
        assert account.composition is not None
        assert account.composition.positions_cny == Decimal(1100)
        assert engine.store.read() == before
        evidence("valuation.json", response.text)
        evidence("cash-statement.json", cash_response.text)
        evidence("q04-ledger.csv", exported.text)


def test_q06_paging_append_filter_and_csv(engine: Engine) -> None:
    # Given: 507条同时间含未知类型、零额、中文和公式文本的记录。
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        for index in range(507):
            state.ledger.append(
                Entry(
                    id=str(index),
                    kind="未知类型" if index % 2 else "fee",
                    currency="CNY",
                    amount=Decimal(-1) if index % 2 else Decimal(0),
                    created_at=NOW,
                    note=" =1+1" if index == 0 else "中文说明" * 100,
                )
            )
        state.cash["CNY"] -= 253
        engine.store.save(connection, state)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        first = LedgerPage.model_validate_json(client.get("/api/ledger/page?limit=100").content)
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            state.ledger.append(
                Entry(id="new", kind="sell", currency="CNY", amount=Decimal(7), created_at=NOW, note="新增")
            )
            state.cash["CNY"] += 7
            engine.store.save(connection, state)
        # When: 使用第一页追加边界继续翻页，完整核对仍读取新增流水。
        ids = [row.id for row in first.items]
        for offset in range(100, 507, 100):
            page = LedgerPage.model_validate_json(
                client.get(f"/api/ledger/page?limit=100&offset={offset}&boundary={first.boundary}").content
            )
            ids.extend(row.id for row in page.items)
        selected = CashStatement.model_validate_json(client.get("/api/cash-statement?kind=fee&boundary=507").content)
        exported = client.get("/api/ledger/export.csv?kind=fee&limit=1&boundary=0")
        rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
        # Then: 原507条恰好一次，类型不改变核对，导出跨全部页。
        assert len(ids) == len(set(ids)) == 507
        assert "new" not in ids
        assert selected.currencies[0].difference == 0
        assert selected.currencies[0].period_net_change == -246
        assert selected.currencies[0].selected_net_change == 0
        assert len(rows) == 255
        assert rows[-1][-1].startswith("'")
        assert client.get("/api/ledger/page?start_date=2026-10-23&end_date=2026-10-22").status_code == 422
        assert client.get("/api/ledger/page?start_date=wrong").status_code == 422
        assert client.get("/api/ledger/page?limit=101").status_code == 422
        assert client.get("/api/ledger/page?boundary=999").status_code == 409
        empty = LedgerPage.model_validate_json(client.get("/api/ledger/page?offset=999").content)
        assert empty.items == []
        assert empty.total == 508
        assert client.get("/api/ledger/page?currency=USD").json()["total"] == 0
        evidence("q06-pagination.json", first.model_dump_json())
        evidence("q06-filtered.csv", exported.text)
        evidence("q06-after-append.json", selected.model_dump_json())


def test_q07_difference_dates_and_no_mutation(engine: Engine) -> None:
    # Given: 北京时间零点前后和故意的差额。
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        for index, hour in enumerate((15, 16)):
            state.ledger.append(
                Entry(
                    id=str(index),
                    kind="legacy",
                    currency="USD",
                    amount=Decimal(10),
                    created_at=datetime(2026, 10, 21, hour, tzinfo=UTC),
                    note="无关联记录",
                )
            )
        state.cash["USD"] = Decimal(21)
        engine.store.save(connection, state)
    before = engine.store.read()
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        # When: 请求北京时间10月22日。
        result = CashStatement.model_validate_json(
            client.get("/api/cash-statement?currency=USD&start_date=2026-10-22&end_date=2026-10-22").content
        )
        page = LedgerPage.model_validate_json(
            client.get("/api/ledger/page?start_date=2026-10-22&end_date=2026-10-22").content
        )
        # Then: 前一日现金成为期初，差额保留，旧关联不猜测。
        assert result.currencies[0].opening_balance == 10
        assert result.currencies[0].closing_balance == 20
        assert result.currencies[0].difference == 1
        assert len(page.items) == 1
        assert page.items[0].order_id is None
        assert engine.store.read() == before
        evidence("q07-difference.json", result.model_dump_json())


def test_q08_snapshot_range_endpoints_archive_and_csv(engine: Engine) -> None:
    # Given: 实际记录首末完整，中间缺日，第四日missing。
    with engine.store.transaction() as connection:
        state = engine.store.load(connection).model_copy(update={"created_at": NOW})
        engine.store.save(connection, state)
    capture(engine, NOW)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["CNY"] += 1200
        engine.store.save(connection, state)
    capture(engine, NOW + timedelta(days=2))
    missing = ValuationSnapshot(
        id=str(uuid4()),
        round_id=state.id,
        day=(NOW + timedelta(days=3)).date(),
        captured_at=NOW + timedelta(days=3),
        total_assets_cny=None,
        pnl_cny=None,
        status="missing",
        issues=["受控缺失"],
        price_times={},
    )
    with engine.store.transaction() as connection:
        _ = connection.execute(
            "INSERT INTO snapshots VALUES(?,?,?)", (state.id, str(missing.day), missing.model_dump_json())
        )
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW + timedelta(days=3))
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        prefix = f"/api/rounds/{state.id}/snapshots"
        # When: 归档自定日期、快捷范围及完整CSV读取。
        result = SnapshotRange.model_validate_json(
            client.get(prefix + "/range?range=custom&start_date=2026-10-22&end_date=2026-10-24").content
        )
        invalid = SnapshotRange.model_validate_json(client.get(prefix + "/range").content)
        for days in (7, 30, 90):
            quick = SnapshotRange.model_validate_json(client.get(prefix + f"/range?range={days}d").content)
            assert quick.end_date == missing.day
            assert (quick.end_date - quick.start_date).days == days - 1
        exported = client.get(prefix + "/export.csv")
        # Then: 不跳过missing末日，归档锚定归档日，不串新轮。
        assert result.change_cny == 1200
        assert result.missing_days == 1
        assert not result.continuous
        assert invalid.change_cny is None
        assert invalid.last is not None
        assert invalid.last.status == "missing"
        rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
        assert rows[-1][3:5] == ["", ""]
        assert client.get("/api/snapshots").json() == []
        assert client.get(prefix + "/range?range=custom").status_code == 422
        single = client.get(prefix + "/range?range=custom&start_date=2026-10-22&end_date=2026-10-22")
        assert single.json()["change_cny"] is None
        empty = client.get(prefix + "/range?range=custom&start_date=2026-01-01&end_date=2026-01-02")
        assert empty.json()["recorded_days"] == 0
        evidence("snapshot-range.json", result.model_dump_json())
        evidence("q08-invalid-endpoint.json", invalid.model_dump_json())
        evidence("q08-snapshots.csv", exported.text)
