import { expect, test } from "bun:test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PortfolioView } from "../src/Portfolio";
import type { Portfolio } from "../src/schema";

test.each([
	["12.50", "gain", "+¥12.50"],
	["-12.50", "loss", "-¥12.50"],
	["0.00", undefined, "¥0.00"],
	[null, undefined, "—"],
] as const)(
	"本轮收益 %p 使用独立配色并保留金额文字",
	(pnl, expectedClass, expectedText) => {
		const client = new QueryClient();
		client.setQueryData(["portfolio"], {
			round_id: "controlled-color",
			initial_capital: "1000000.00",
			total_assets_cny: "1000000.00",
			pnl_cny: pnl,
			valuation_status: "complete",
			cash: [],
			positions: [],
			warnings: [],
			worker: { last_run: null, status: "starting" },
		} satisfies Portfolio);
		try {
			const html = renderToStaticMarkup(
				createElement(
					QueryClientProvider,
					{ client },
					createElement(PortfolioView, {
						onExplore: () => {},
						onCash: () => {},
						onSymbol: () => {},
						onOrders: () => {},
					}),
				),
			);
			const earnings = html.match(
				/本轮收益 <strong(?: class="([^"]*)")?>(.*?)<\/strong>/,
			);
			expect(earnings).not.toBeNull();
			expect(earnings?.[1]).toBe(expectedClass);
			expect(earnings?.[2]).toBe(expectedText);
		} finally {
			client.clear();
		}
	},
);
