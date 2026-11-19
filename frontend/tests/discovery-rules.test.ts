import { describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { InstrumentList } from "../src/Discover";
import { DiscoveryResults } from "../src/DiscoveryResults";
import type { Instrument } from "../src/schema";

const instrument: Instrument = {
	symbol: "TEST",
	name: "受控证券",
	market: "US",
	currency: "USD",
	kind: "stock",
	lot_size: 1,
	tick_size: "0.01",
	tradable: true,
	restriction: "受控共同规则",
};

function render(item: Instrument) {
	return renderToStaticMarkup(
		createElement(DiscoveryResults, {
			items: [item],
			onSelect: () => {},
			onToggle: () => {},
			contains: () => false,
			pending: false,
			sharedRules: new Map([["US", "受控共同规则"]]),
		}),
	);
}

describe("发现列表逐行规则", () => {
	it("仅在市场与完整规则都匹配时省略可交易行的重复文字", () => {
		expect(render(instrument)).not.toContain("受控共同规则");
		expect(render({ ...instrument, market: "CN" })).toContain("受控共同规则");
	});
	it("保留相同市场中不同的规则及文字后缀例外", () => {
		expect(
			render({ ...instrument, restriction: "受控共同规则，仅限整数份" }),
		).toContain("受控共同规则，仅限整数份");
	});
	it("观察行即使规则相同也保留规则和观察资格", () => {
		const html = render({ ...instrument, tradable: false });
		expect(html).toContain("受控共同规则");
		expect(html).toContain("仅观察");
	});
});

describe("发现列表实际归并", () => {
	it("同屏有例外时仍归并重复完整规则，并保留例外和观察行", () => {
		const html = renderToStaticMarkup(
			createElement(InstrumentList, {
				items: [
					instrument,
					{ ...instrument, symbol: "SECOND" },
					{ ...instrument, symbol: "SPECIAL", restriction: "受控例外规则" },
					{ ...instrument, symbol: "WATCH", tradable: false },
				],
				onSelect: () => {},
				onToggle: () => {},
				contains: () => false,
				pending: false,
				empty: "无记录",
			}),
		);
		expect(html.match(/受控共同规则/g)?.length).toBe(2);
		expect(html).toContain("受控例外规则");
		expect(html).toContain("仅观察");
	});
	it("归并覆盖分页后的完整列表，首屏仍显示公共规则", () => {
		const items = Array.from({ length: 25 }, (_, index) => ({
			...instrument,
			symbol: `PAGE${index}`,
			restriction:
				index === 0 || index === 24 ? "跨页共同规则" : `独立规则${index}`,
		}));
		const html = renderToStaticMarkup(
			createElement(InstrumentList, {
				items,
				onSelect: () => {},
				onToggle: () => {},
				contains: () => false,
				pending: false,
				empty: "无记录",
			}),
		);
		expect(html.match(/跨页共同规则/g)?.length).toBe(1);
		expect(html).toContain("再显示 1 只");
		expect(html).not.toContain("PAGE24");
	});
});

it("同市场两个重复组只归并默认组，其他组逐行对应", () => {
	const html = renderToStaticMarkup(
		createElement(InstrumentList, {
			items: [
				instrument,
				{ ...instrument, symbol: "SECOND" },
				{ ...instrument, symbol: "OTHER1", restriction: "另一组规则" },
				{ ...instrument, symbol: "OTHER2", restriction: "另一组规则" },
			],
			onSelect: () => {},
			onToggle: () => {},
			contains: () => false,
			pending: false,
			empty: "无记录",
		}),
	);
	expect(html.match(/受控共同规则/g)?.length).toBe(1);
	expect(html.match(/另一组规则/g)?.length).toBe(2);
	expect(html).toContain("除行内另注外");
});
