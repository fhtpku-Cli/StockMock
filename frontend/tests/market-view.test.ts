import { describe, expect, test } from "bun:test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ChangeLine } from "../src/ChangeLine";
import {
	changeDisplay,
	chartGeometry,
	compareWithReference,
	rangeRows,
	referenceSymbol,
	tradeMarks,
} from "../src/marketView";
import { CANCEL_CONFIRM } from "../src/Orders";
import { PriceHistory } from "../src/PriceHistory";
import {
	historySchema,
	orderSchema,
	portfolioSchema,
	quoteSchema,
} from "../src/schema";

const row = (time: string, close: string, complete: boolean | null = true) => ({
	time,
	open: close,
	high: close,
	low: close,
	close,
	volume: "1",
	retrieved_at: "2026-10-30T12:00:00Z",
	complete,
});
const days = (count: number) =>
	Array.from({ length: count }, (_, index) =>
		row(
			new Date(Date.UTC(2025, 0, 1 + index)).toISOString().slice(0, 10),
			String(100 + index),
		),
	);

describe("走势范围", () => {
	test("1个月/3个月/1年分别取最近 21/63/252 个交易日", () => {
		const rows = days(252).reverse();
		expect(rangeRows(rows, "1m")).toHaveLength(21);
		expect(rangeRows(rows, "3m")).toHaveLength(63);
		expect(rangeRows(rows, "1y")).toHaveLength(252);
		expect(rangeRows(rows, "1m").at(-1)?.time).toBe(days(252).at(-1)?.time);
	});
});

describe("涨跌显示", () => {
	const now = new Date("2026-10-30T20:00:00+08:00");
	const value = {
		previous_close: "100",
		change: "1.5",
		change_percent: "1.5",
		change_day: "2026-10-30",
		change_base_day: "2026-10-29",
		change_status: "available" as const,
	};
	test("市场今天显示今日涨跌并注明供应商昨收日期", () => {
		const result = changeDisplay(value, "CN", now);
		expect(result).toEqual({
			kind: "value",
			label: "今日涨跌",
			change: "1.5",
			percent: "+1.50%",
			direction: "up",
			base: "相对供应商昨收（10月29日）",
		});
		expect(
			renderToStaticMarkup(
				createElement(ChangeLine, { value: result, currency: "CNY" }),
			),
		).toContain("今日涨跌 +¥1.50 / +1.50%");
	});
	test("美股北京时间次日凌晨仍是美东今天，美东新一天显示日期", () => {
		const morning = new Date("2026-10-31T03:00:00+08:00");
		expect(changeDisplay(value, "US", morning).label).toBe("今日涨跌");
		const next = new Date("2026-10-31T13:00:00+08:00");
		expect(changeDisplay(value, "US", next).label).toBe("10月30日 涨跌");
	});
	test("下跌与各状态文字，旧后端缺字段显示不可用", () => {
		const down = changeDisplay(
			{ ...value, change: "-2", change_percent: "-2" },
			"CN",
			now,
		);
		expect(down.kind === "value" && [down.direction, down.percent]).toEqual([
			"down",
			"-2.00%",
		]);
		const texts = {
			pre_open: "开盘前不显示今日涨跌",
			no_previous_close: "昨收不可用",
			no_price: "不可用",
			quote_changed: "报价已更新，刷新后显示",
			market_disabled: "该市场今日涨跌暂不提供",
			unknown: "涨跌暂不可用",
		} as const;
		for (const [status, text] of Object.entries(texts))
			expect(
				changeDisplay(
					{ change_status: status as keyof typeof texts },
					"CN",
					now,
				),
			).toEqual({ kind: "message", label: "今日涨跌", text });
		expect(changeDisplay({}, "CN", now)).toEqual({
			kind: "message",
			label: "今日涨跌",
			text: "不可用",
		});
	});
});

describe("同期参照", () => {
	test("只用共有的定稿日期，不补点", () => {
		const own = [
			row("2026-10-27", "100"),
			row("2026-10-28", "105"),
			row("2026-10-29", "110"),
			row("2026-10-30", "120", false),
		];
		const reference = [
			row("2026-10-28", "200"),
			row("2026-10-29", "210"),
			row("2026-10-30", "220"),
		];
		const result = compareWithReference(own, reference, "3m");
		expect(result.ok && [result.start, result.end]).toEqual([
			"2026-10-28",
			"2026-10-29",
		]);
		expect(result.ok && result.own.toFixed(3)).toBe("4.762");
		expect(result.ok && result.reference.toFixed(1)).toBe("5.0");
	});
	test("只有盘中线或共有日期少于 2 个时不可用，未知定稿不参与", () => {
		const intraday = [
			row("2026-10-29", "1", false),
			row("2026-10-30", "2", false),
		];
		expect(compareWithReference(intraday, intraday, "3m").ok).toBe(false);
		const unknown = [
			row("2026-10-29", "1", null),
			row("2026-10-30", "2", null),
		];
		expect(compareWithReference(unknown, unknown, "3m").ok).toBe(false);
		expect(
			compareWithReference(
				[row("2026-10-29", "1")],
				[row("2026-10-29", "1")],
				"3m",
			).ok,
		).toBe(false);
	});
	test("参照证券本身与港股观察不显示参照行", () => {
		expect(referenceSymbol("CN", "600519.SS")?.symbol).toBe("510300.SS");
		expect(referenceSymbol("US", "AAPL")?.symbol).toBe("SPY");
		expect(referenceSymbol("CN", "510300.SS")).toBeNull();
		expect(referenceSymbol("US", "SPY")).toBeNull();
		expect(referenceSymbol("HK", "0700.HK")).toBeNull();
	});
});

const order = (
	id: string,
	side: "buy" | "sell",
	filled_at: string,
	fill_price: string,
) =>
	orderSchema.parse({
		id,
		request_id: `${id}-request`,
		symbol: "AAPL",
		side,
		order_type: "limit",
		quantity: 100,
		limit_price: "300",
		price_bound: "300",
		status: "filled",
		status_reason: "",
		created_at: filled_at,
		active_at: filled_at,
		expires_at: filled_at,
		cancel_at: null,
		filled_at,
		confirmed_at: "2026-11-02T00:00:00Z",
		fill_price,
		fee: "1",
		currency: "USD",
		reserved_cny: "0",
		reserved_foreign: "0",
		fx_rate: null,
		fx_source: null,
		fx_time: null,
		reason: "",
		target_price: null,
		stop_price: null,
	});

describe("成交标记", () => {
	const visible = [
		row("2026-10-28", "100"),
		row("2026-10-29", "110"),
		row("2026-10-30", "105"),
	];
	test("按美东当地成交日标注，范围外不标，当日无日线只进列表", () => {
		const marks = tradeMarks(
			[
				// 北京时间 10-30 03:00 是美东 10-29 15:00。
				order("a", "buy", "2026-10-29T19:00:00Z", "120"),
				order("b", "sell", "2026-10-27T15:00:00Z", "90"),
				order("c", "sell", "2026-10-31T15:00:00Z", "95"),
			],
			"AAPL",
			"US",
			visible,
		);
		expect(marks.map((m) => [m.id, m.day, m.onChart])).toEqual([
			["a", "2026-10-29", true],
			["c", "2026-10-31", false],
		]);
		const geometry = chartGeometry(visible, marks);
		expect([geometry.low, geometry.high]).toEqual([100, 120]);
		expect(geometry.y(120)).toBe(30);
		expect(geometry.x(1)).toBe(350);
	});
	test.each([
		["缺末日", ["2026-11-09"], false],
		["内部缺口", ["2026-11-09", "2026-11-11"], false],
		["对应日线补齐", ["2026-11-09", "2026-11-10", "2026-11-11"], true],
	] as const)(
		"%s 时保留成交，只在对应日期存在时画标记",
		(_scenario, dates, onChart) => {
			const orders = [order("gap", "sell", "2026-11-11T00:00:00Z", "90")];
			const rows = dates.map((day) => row(day, "100"));
			const marks = tradeMarks(orders, "AAPL", "US", rows);
			expect(marks).toEqual([
				{
					id: "gap",
					side: "sell",
					day: "2026-11-10",
					price: "90",
					quantity: 100,
					onChart,
				},
			]);
			const geometry = chartGeometry(rows, marks);
			expect([geometry.low, geometry.high]).toEqual(
				onChart ? [90, 100] : [100, 100],
			);
		},
	);
	test.each(["CN", "HK"] as const)("%s 按当地日期选择对应日线", (market) => {
		const orders = [order("local-day", "buy", "2026-11-09T16:30:00Z", "120")];
		const rows = [row("2026-11-09", "100"), row("2026-11-10", "100")];
		const marks = tradeMarks(orders, "AAPL", market, rows);
		expect(marks.map((mark) => [mark.day, mark.onChart])).toEqual([
			["2026-11-10", true],
		]);
	});
	test("其他证券与未成交委托不标注", () => {
		const other = {
			...order("d", "buy", "2026-10-29T19:00:00Z", "1"),
			symbol: "MSFT",
		};
		const open = {
			...order("e", "buy", "2026-10-29T19:00:00Z", "1"),
			status: "accepted" as const,
		};
		expect(tradeMarks([other, open], "AAPL", "US", visible)).toEqual([]);
	});
});

describe("新旧搭配", () => {
	test("旧后端响应缺少新字段仍可解析", () => {
		expect(
			quoteSchema.parse({
				symbol: "AAPL",
				price: "1",
				currency: "USD",
				market_time: "2026-10-30T20:00:00Z",
				retrieved_at: "2026-10-30T20:00:00Z",
				source: "s",
				status: "reference",
				message: "",
			}).change_status,
		).toBeUndefined();
		expect(
			historySchema.parse([
				{
					time: "2026-10-30",
					open: "1",
					high: "1",
					low: "1",
					close: "1",
					volume: "1",
				},
			])[0]?.complete,
		).toBeUndefined();
	});
	test("持仓新字段可为空并拒绝未知状态", () => {
		const account = {
			round_id: "r",
			initial_capital: "1000000",
			total_assets_cny: null,
			pnl_cny: null,
			valuation_status: "",
			cash: [],
			worker: { last_run: null, status: "ok" },
			warnings: [],
			positions: [
				{
					symbol: "AAPL",
					name: "Apple",
					currency: "USD",
					quantity: 1,
					sellable: 1,
					market: "US",
					cost: "1",
					pnl_percent: null,
					market_value_cny: null,
					weight_percent: null,
					price_time: null,
					reserved: 0,
					average_cost: "1",
					market_price: null,
					market_value: null,
					pnl: null,
					valuation_status: "",
					change_status: "no_price",
					change: null,
				},
			],
		};
		expect(portfolioSchema.parse(account).positions[0]?.change_status).toBe(
			"no_price",
		);
		const broken = structuredClone(account);
		Object.assign(broken.positions[0] ?? {}, { change_status: "surprise" });
		expect(portfolioSchema.safeParse(broken).success).toBe(false);
	});
	test("撤单确认文字说明不能撤回且仍可能成交", () => {
		expect(CANCEL_CONFIRM).toContain("不能撤回");
		expect(CANCEL_CONFIRM).toContain("期间仍可能成交");
	});
});

describe("日线收盘状态文案", () => {
	test.each([
		[
			false,
			"2026-10-30T12:00:00Z",
			"（取得于北京时间 20:00，是否已收盘未确认）",
		],
		[false, null, "（取得时刻未知，是否已收盘未确认）"],
		[null, null, "最后一根是否已收盘：未知。"],
		[undefined, null, "最后一根是否已收盘：未知。"],
	] as const)(
		"complete=%p，取得时刻=%p 使用中性或未知文案",
		(complete, retrieved_at, expected) => {
			const client = new QueryClient();
			client.setQueryData(
				["orders"],
				[order("caption-trade", "buy", "2026-10-29T19:00:00Z", "100")],
			);
			const rows = [
				row("2026-10-29", "100"),
				{ ...row("2026-10-30", "101"), complete, retrieved_at },
			];
			try {
				const html = renderToStaticMarkup(
					createElement(
						QueryClientProvider,
						{ client },
						createElement(PriceHistory, {
							symbol: "AAPL",
							name: "受控日线",
							market: "US",
							currency: "USD",
							rows,
						}),
					),
				);
				expect(html).toContain(expected);
				expect(html).not.toContain("盘中");
				if (complete === false)
					expect(html).toContain(
						"最后一根为取得时的价格，是否已收盘未确认（日线缓存最长 1 小时，可能比页头价格旧）。",
					);
				expect(html).toContain("最后一根为取得时的价格");
			} finally {
				client.clear();
			}
		},
	);
});
