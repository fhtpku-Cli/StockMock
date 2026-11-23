import { describe, expect, it } from "bun:test";
import {
	QueryClient,
	QueryClientProvider,
	onlineManager,
} from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Instrument } from "../src/schema";
import { useWatchlist } from "../src/useWatchlist";
import { instrumentSearchSchema, quoteSchema } from "../src/schema";

describe("在线发现接口边界", () => {
	it("接受仅观察证券的未知交易规则而不填造每手与价位", () => {
		const response = {
			items: [
				{
					symbol: "IBM",
					name: "IBM",
					market: "US",
					currency: "USD",
					kind: "stock",
					lot_size: null,
					tick_size: null,
					tick_rule: null,
					min_buy_quantity: null,
					quantity_step: null,
					tradable: false,
					restriction: "仅观察",
				},
			],
			source: "controlled-test",
			message: "受控接口验证",
		};
		const parsed = instrumentSearchSchema.parse(response);
		expect(parsed.items[0]?.lot_size).toBeNull();
		expect(parsed.items[0]?.tick_size).toBeNull();
		expect(parsed.items[0]?.tradable).toBe(false);
	});
	it("拒绝缺少证券身份的搜索项", () => {
		const response = {
			items: [{ name: "无代码" }],
			source: "controlled-test",
			message: "受控接口验证",
		};
		const parsed = instrumentSearchSchema.safeParse(response);
		expect(parsed.success).toBe(false);
	});
	it("观察报价保留真实价格并接受未知价位", () => {
		const response = {
			symbol: "IBM",
			price: "200.123",
			currency: "USD",
			market_time: "2026-10-23T00:00:00Z",
			retrieved_at: "2026-10-23T00:10:00Z",
			source: "controlled-test",
			tick_size: null,
			status: "reference",
			message: "受控报价验证",
		};
		const parsed = quoteSchema.parse(response);
		expect(parsed.price).toBe("200.123");
		expect(parsed.tick_size).toBeNull();
	});
});

it.each([false, true])(
	"自选写入遇到断网立即拒绝且不排队（已收藏 %p）",
	async (saved) => {
		const item: Instrument = {
			symbol: "IBM",
			name: "IBM",
			market: "US",
			currency: "USD",
			kind: "stock",
			lot_size: 1,
			tick_size: "0.01",
			tradable: true,
			restriction: null,
		};
		const client = new QueryClient();
		client.setQueryData(["watchlist"], saved ? [item] : []);
		let toggle!: ReturnType<typeof useWatchlist>["toggle"]["mutateAsync"];
		function WatchProbe() {
			toggle = useWatchlist().toggle.mutateAsync;
			return null;
		}
		renderToStaticMarkup(
			createElement(QueryClientProvider, { client }, createElement(WatchProbe)),
		);
		const original = Object.getOwnPropertyDescriptor(navigator, "onLine");
		const wasOnline = onlineManager.isOnline();
		Object.defineProperty(navigator, "onLine", {
			configurable: true,
			value: false,
		});
		client.mount();
		onlineManager.setOnline(false);
		const result = toggle(item).catch((e: unknown) => e);
		try {
			expect(client.getMutationCache().getAll()[0]?.state.isPaused).toBe(false);
			expect(await result).toMatchObject({ code: "offline" });
		} finally {
			onlineManager.setOnline(true);
			await result;
			client.unmount();
			client.clear();
			onlineManager.setOnline(wasOnline);
			if (original) Object.defineProperty(navigator, "onLine", original);
			else Reflect.deleteProperty(navigator, "onLine");
		}
	},
);
