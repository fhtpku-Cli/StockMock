import { expect, test } from "bun:test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Position } from "../src/analysis";
import { Detail } from "../src/Detail";
import { Positions } from "../src/Positions";
import type { Instrument, Quote } from "../src/schema";

const position = (
	symbol: string,
	status: Position["change_status"],
): Position => ({
	symbol,
	name: symbol,
	market: "US",
	currency: "USD",
	quantity: 1,
	sellable: 1,
	reserved: 0,
	average_cost: "100",
	cost: "100",
	market_price: "101",
	market_value: "101",
	pnl: "1",
	valuation_status: "reference",
	market_value_cny: "707",
	weight_percent: "1",
	pnl_percent: "1",
	price_time: null,
	change_status: status,
	change: "1",
	change_percent: "1",
	change_day: "2026-10-30",
});
const renderPositions = (positions: readonly Position[]) =>
	renderToStaticMarkup(
		createElement(Positions, {
			positions,
			onSymbol: () => {},
			onExplore: () => {},
		}),
	);

test("全部持仓市场关闭时，桌面整列和手机涨跌行都隐藏", () => {
	const positions = [
		position("AAPL", "market_disabled"),
		position("SPY", "market_disabled"),
	];
	const html = renderPositions(positions);
	expect(html).not.toContain('class="change-column"');
	expect(html).not.toContain('class="change-inline"');
	expect(html).not.toContain("data-change=");
	expect(html).toContain("原币浮动收益 / 收益率");
});

test("混合持仓保留整列，关闭行桌面空白且没有手机涨跌行", () => {
	const positions = [
		position("AAPL", "market_disabled"),
		position("SPY", "available"),
	];
	const html = renderPositions(positions);
	const disabled =
		html.match(/<tr data-position="AAPL">(.*?)<\/tr>/)?.[1] ?? "";
	expect(html).toContain("今日涨跌（每股）");
	expect(disabled).toContain('<td class="change-column"></td>');
	expect(disabled).not.toContain('class="change-inline"');
	expect(disabled).not.toContain("data-change=");
	expect(html).toContain('data-change="value"');
});

test.each([
	["pre_open", "开盘前不显示今日涨跌"],
	["no_previous_close", "昨收不可用"],
] as const)("持仓 %s 仍保留桌面和手机状态文字", (status, text) => {
	const html = renderPositions([position("AAPL", status)]);
	expect(html).toContain("今日涨跌（每股）");
	expect(html).toContain('class="change-inline"');
	expect(html.split(text)).toHaveLength(3);
});

const instrument: Instrument = {
	symbol: "AAPL",
	name: "Apple",
	market: "US",
	currency: "USD",
	kind: "stock",
	lot_size: 1,
	tick_size: "0.01",
	tradable: true,
	restriction: null,
};
test.each([
	["market_disabled", null],
	["pre_open", "开盘前不显示今日涨跌"],
	["no_previous_close", "昨收不可用"],
] as const)("详情页 %s 只对关闭市场隐藏涨跌行", (status, text) => {
	const client = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	client.setQueryData(["quote", "AAPL"], {
		symbol: "AAPL",
		price: "101",
		currency: "USD",
		market_time: "2026-10-30T20:00:00Z",
		retrieved_at: "2026-10-30T20:00:00Z",
		source: "受控数据",
		status: "reference",
		message: "",
		change_status: status,
	} satisfies Quote);
	try {
		const html = renderToStaticMarkup(
			createElement(
				QueryClientProvider,
				{ client },
				createElement(Detail, {
					instrument,
					onClose: () => {},
					onTrade: () => {},
					onEnabled: () => {},
				}),
			),
		);
		if (text === null) expect(html).not.toContain("data-change=");
		else expect(html).toContain(text);
		expect(html).toContain('class="quote-price"');
	} finally {
		client.clear();
	}
});

const changeCases = [
	["CN", "CNY", "600519.SS", "0.570", "+¥0.570", "+0.57%", "up"],
	["CN", "CNY", "600519.SS", "-0.570", "-¥0.570", "-0.57%", "down"],
	["CN", "CNY", "600519.SS", "0.000", "¥0.000", "0.00%", "flat"],
	["HK", "HKD", "0700.HK", "0.570", "+HK$0.570", "+0.57%", "up"],
	["HK", "HKD", "0700.HK", "-0.570", "-HK$0.570", "-0.57%", "down"],
	["HK", "HKD", "0700.HK", "0.000", "HK$0.000", "0.00%", "flat"],
	["US", "USD", "AAPL", "0.570", "+US$0.570", "+0.57%", "up"],
	["US", "USD", "AAPL", "-0.570", "-US$0.570", "-0.57%", "down"],
	["US", "USD", "AAPL", "0.000", "US$0.000", "0.00%", "flat"],
] as const;

test.each(changeCases)(
	"持仓 %s %s %s 涨跌 %s 使用币种格式且保留方向符号",
	(market, currency, symbol, change, amount, percent, direction) => {
		const value = {
			...position(symbol, "available"),
			market,
			currency,
			change,
			change_percent: change,
		};
		const html = renderPositions([value]);
		const lines = [
			...html.matchAll(
				/<small class="change-line ([^"]+)" data-change="value">(.*?)<\/small>/g,
			),
		];
		expect(lines).toHaveLength(2);
		for (const line of lines) {
			expect(line[1]).toBe(direction);
			expect(line[2]).toEndWith(` ${amount} / ${percent}`);
		}
	},
);

test.each(changeCases)(
	"详情 %s %s %s 涨跌 %s 使用币种格式且保留方向符号",
	(market, currency, symbol, change, amount, percent, direction) => {
		const client = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
		client.setQueryData(["quote", symbol], {
			symbol,
			price: "100.570",
			currency,
			market_time: "2026-10-30T20:00:00Z",
			retrieved_at: "2026-10-30T20:00:00Z",
			source: "受控数据",
			status: "reference",
			message: "",
			change_status: "available",
			change,
			change_percent: change,
			change_day: "2026-10-30",
			change_base_day: "2026-10-29",
		} satisfies Quote);
		try {
			const html = renderToStaticMarkup(
				createElement(
					QueryClientProvider,
					{ client },
					createElement(Detail, {
						instrument: { ...instrument, symbol, market, currency },
						onClose: () => {},
						onTrade: () => {},
						onEnabled: () => {},
					}),
				),
			);
			const line = html.match(
				/<small class="change-line ([^"]+)" data-change="value">(.*?)<span/,
			);
			expect(line?.[1]).toBe(direction);
			expect(line?.[2]).toEndWith(` ${amount} / ${percent}`);
		} finally {
			client.clear();
		}
	},
);
