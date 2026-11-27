import { expect, test } from "bun:test";
import {
	episodesKey,
	eventDetailTexts,
	eventMainText,
	eventTimeText,
	holdingText,
	episodeSummary,
	peakText,
	reconcileText,
	RECONCILE_MISMATCH,
} from "../src/episodeView";
import {
	type Episode,
	type EpisodeEvent,
	type EpisodesData,
	episodeEventSchema,
	episodeSchema,
	episodesSchema,
} from "../src/schema";

// 受控数据只覆盖第 7 节最终契约，不连接任何账户服务。
const event = (overrides: Partial<EpisodeEvent>): EpisodeEvent =>
	episodeEventSchema.parse({
		kind: "first_buy",
		at: "2026-11-26T01:31:00Z",
		market_date: "2026-11-26",
		currency: "CNY",
		quantity: 100,
		price: "1465.500",
		fee: "73.28",
		position_after: 100,
		order_id: "order-1",
		ratio: null,
		cash_per_share: null,
		cash_amount: null,
		payment_status: null,
		note: null,
		...overrides,
	});

const episode = (overrides: Partial<Episode>): Episode =>
	episodeSchema.parse({
		index: 1,
		status: "closed",
		opened_at: "2026-11-01T01:31:00Z",
		closed_at: "2026-11-03T03:00:00Z",
		opened_market_date: "2026-11-01",
		closed_market_date: "2026-11-03",
		holding_days: 2,
		buy_count: 2,
		sell_count: 1,
		peak_quantity: 200,
		boundary_note: null,
		events: [],
		...overrides,
	});

const data = (overrides: Partial<EpisodesData>): EpisodesData =>
	episodesSchema.parse({
		symbol: "600519.SS",
		name: "受控茅台",
		currency: "CNY",
		round_id: "r-1",
		as_of: "2026-11-28T12:00:00+00:00",
		market_today: "2026-11-28",
		reconciled: true,
		reconcile_note: null,
		episodes: [],
		outside_events: [],
		...overrides,
	});

test("折叠摘要：进行中按已持有天数，与详案示例一致", () => {
	expect(
		episodeSummary(
			episode({
				index: 2,
				status: "open",
				opened_at: "2026-11-02T01:31:00Z",
				closed_at: null,
				opened_market_date: "2026-11-03",
				closed_market_date: null,
				holding_days: 25,
				buy_count: 3,
				sell_count: 1,
			}),
		),
	).toBe("第 2 段 · 进行中 · 11/03 首买 · 已持有 25 天 · 买 3 次 卖 1 次");
});

test("折叠摘要：已清仓带清仓日与自然日天数", () => {
	expect(episodeSummary(episode())).toBe(
		"第 1 段 · 已清仓 · 11/01 首买 · 11/03 清仓 · 持有 2 天 · 买 2 次 卖 1 次",
	);
});

test("折叠摘要：同日买卖记当日清仓，不写零天", () => {
	expect(
		episodeSummary(
			episode({
				holding_days: 0,
				closed_at: "2026-11-01T06:00:00Z",
				closed_market_date: "2026-11-01",
			}),
		),
	).toBe("第 1 段 · 已清仓 · 11/01 首买 · 当日清仓 · 买 2 次 卖 1 次");
});

test("折叠摘要：边界未知时附加文字说明，不靠颜色", () => {
	const note = "拆合股前数量核对不上，本段首买或清仓边界可能不准";
	expect(
		episodeSummary(episode({ boundary_note: note })).endsWith(` · ${note}`),
	).toBe(true);
});

test("持有天数文案：进行中与已清仓分别表述", () => {
	expect(
		holdingText(episode({ status: "open", holding_days: 25 })),
	).toBe("已持有 25 天");
	expect(holdingText(episode({ status: "open", holding_days: 0 }))).toBe(
		"已持有 0 天",
	);
	expect(holdingText(episode({ holding_days: 2 }))).toBe("持有 2 天");
	expect(holdingText(episode({ holding_days: 0 }))).toBe("当日清仓");
});

test("成交事件主行：首买/加仓为买入，减仓/清仓为卖出，数量×价格按原币", () => {
	expect(eventMainText(event({ kind: "first_buy" }))).toBe(
		"买入 100 股/份 × ¥1,465.500",
	);
	expect(
		eventMainText(event({ kind: "add", currency: "USD", price: "100.5" })),
	).toBe("买入 100 股/份 × US$100.50");
	expect(eventMainText(event({ kind: "reduce", quantity: 50 }))).toBe(
		"卖出 50 股/份 × ¥1,465.500",
	);
	expect(eventMainText(event({ kind: "close", quantity: 100 }))).toBe(
		"卖出 100 股/份 × ¥1,465.500",
	);
});

test("成交事件次行：费用与事件后持仓", () => {
	expect(eventDetailTexts(event({ kind: "first_buy" }))).toEqual([
		"费用 ¥73.28",
		"事件后持仓 100 股/份",
	]);
});

test("拆合股显示比例，只给事件后持仓", () => {
	const split = event({
		kind: "split",
		at: "2026-11-02T16:00:00Z",
		market_date: "2026-11-03",
		quantity: 200,
		price: null,
		fee: null,
		position_after: 200,
		order_id: null,
		ratio: "2:1",
	});
	expect(eventMainText(split)).toBe("比例 2:1");
	expect(eventDetailTexts(split)).toEqual(["事件后持仓 200 股/份"]);
});

test("股息显示每股金额、合计金额与到账或应收", () => {
	const dividend = event({
		kind: "dividend",
		quantity: 100,
		price: null,
		fee: null,
		order_id: null,
		cash_per_share: "1.500",
		cash_amount: "150.00",
		payment_status: "paid",
	});
	expect(eventMainText(dividend)).toBe("每股股息 ¥1.500");
	expect(eventDetailTexts(dividend)).toEqual([
		"股息 ¥150.00（已到账）",
		"事件后持仓 100 股/份",
	]);
	expect(
		eventDetailTexts(
			event({
				...dividend,
				payment_status: "receivable",
				cash_amount: "120.00",
				currency: "HKD",
			}),
		),
	).toEqual(["股息 HK$120.00（应收）", "事件后持仓 100 股/份"]);
});

test("异常成交用后端说明文字，不显示数量价格", () => {
	const note = "委托 order-9 缺少 filled_at 与 fill_price，未参与持仓计算";
	const anomaly = event({
		kind: "anomaly",
		quantity: null,
		price: null,
		fee: null,
		position_after: 100,
		order_id: "order-9",
		note,
	});
	expect(eventMainText(anomaly)).toBe(note);
	expect(eventDetailTexts(anomaly)).toEqual(["事件后持仓 100 股/份"]);
	expect(eventMainText(event({ ...anomaly, note: null }))).toBe(
		"成交记录缺少必要字段，未参与持仓计算",
	);
});

test("事件时间为北京时间，市场日不同日时补充市场当地日期", () => {
	expect(eventTimeText(event())).toBe("11/26 09:31（北京时间）");
	expect(
		eventTimeText(
			event({
				at: "2026-11-27T18:00:00Z",
				market_date: "2026-11-27",
			}),
		),
	).toBe("11/28 02:00（北京时间） · 市场日 11/27");
});

test("拆合股与股息只显示生效的市场日期，不显示推算时刻", () => {
	for (const kind of ["split", "dividend"] as const)
		expect(
			eventTimeText(
				event({ kind, at: "2026-10-15T04:00:00Z", market_date: "2026-10-15" }),
			),
		).toBe("10/15 生效（市场当地日期）");
});

test("段内最大持仓：含拆合股时标注口径", () => {
	const withSplit = episode({
		peak_quantity: 300,
		events: [event({ kind: "split", ratio: "2:1" })],
	});
	expect(peakText(withSplit)).toBe("300 股/份（按拆合股后口径）");
	expect(peakText(episode({ peak_quantity: 300 }))).toBe(
		"300 股/份",
	);
});

test("核对提示：不一致时优先用后端说明，缺省用固定文案，一致时不提示", () => {
	expect(reconcileText(data({ reconciled: true }))).toBeNull();
	expect(
		reconcileText(
			data({
				reconciled: false,
				reconcile_note: "当前持仓 100 股/份，重放终值 90 股/份。",
			}),
		),
	).toBe("当前持仓 100 股/份，重放终值 90 股/份。");
	expect(reconcileText(data({ reconciled: false }))).toBe(RECONCILE_MISMATCH);
});

test("查询键以 orders 开头，委托相关失效会一并重取", () => {
	expect(episodesKey("AAPL")).toEqual(["orders", "episodes", "AAPL"]);
});

test("schema 按第 7 节最终契约解析，未知事件类型拒绝", () => {
	const contract = data({
		episodes: [
			episode({
				index: 2,
				status: "open",
				closed_at: null,
				closed_market_date: null,
				holding_days: 2,
				buy_count: 1,
				sell_count: 0,
				peak_quantity: 100,
				events: [event({ kind: "first_buy", price: "1465.500", fee: "73.28" })],
			}),
		],
	});
	expect(contract.episodes[0]?.events[0]?.order_id).toBe("order-1");
	expect(
		episodesSchema.safeParse({
			...contract,
			episodes: [
				{
					...contract.episodes[0],
					events: [{ ...event(), kind: "unknown" }],
				},
			],
		}).success,
	).toBe(false);
});
