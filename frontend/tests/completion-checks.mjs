import { expect } from "@playwright/test";

export async function verifyPreview({ page, preview, side }) {
	const box = page.locator(".preview-box");
	const reference = box
		.locator("dl > div")
		.filter({ has: page.getByText("换汇参考", { exact: true }) });
	await expect(
		page.getByRole("button", { name: "确认提交委托" }),
	).toBeEnabled();
	await expect(box).not.toContainText("1 USD = 1 CNY");
	if (side === "buy" && Number(preview.reserved_cny) > 0) {
		expect(preview.fx_rate).toBe("7");
		expect(preview.fx_source).toBe("controlled-test");
		expect(preview.fx_time).toMatch(/^2026-10-22T/);
		await expect(reference).toContainText("1 USD = 7 CNY");
		await expect(reference).toContainText("controlled-test");
		await expect(reference).toContainText("10/22 23:00");
		await expect(box).toContainText("自动换汇");
	} else {
		expect(preview.fx_rate).toBeNull();
		expect(preview.fx_source).toBeNull();
		expect(preview.fx_time).toBeNull();
		await expect(reference).toHaveCount(0);
		await expect(box).toContainText(
			side === "sell" ? "卖出款保留原币" : "使用已有外币现金",
		);
	}
	await page.setViewportSize({ width: 390, height: 844 });
	expect(
		await page
			.getByRole("dialog")
			.evaluate((el) => el.scrollWidth <= el.clientWidth),
	).toBe(true);
	expect(
		await page.evaluate(
			() => document.documentElement.scrollWidth <= innerWidth,
		),
	).toBe(true);
	await box.scrollIntoViewIfNeeded();
}

export async function verifyArchive({ page, archive, shot }) {
	// 已归档的真实引擎记录必须通过与当前委托相同的只读展示提供完整依据。
	const mutations = [];
	const track = (request) => {
		if (
			request.url().includes("/api/") &&
			!["GET", "HEAD"].includes(request.method())
		)
			mutations.push(request.url());
	};
	page.on("request", track);
	const orders = archive
		.locator("section")
		.filter({ has: page.getByRole("heading", { name: /历史委托/ }) });
	const buy = orders.locator(".order-row").filter({ hasText: "买入 10 股" });
	await buy.locator("summary").click();
	await expect(buy).toContainText("1 USD = 7 CNY");
	await expect(buy).toContainText("controlled-test");
	await expect(buy).toContainText("US$120.00 / US$80.00");
	await expect(buy).toContainText("费用 US$0.50");
	await expect(
		buy.locator("dl > div").filter({ hasText: "成交时间 / 确认时间" }),
	).toContainText("10/22 23:05");
	await buy.scrollIntoViewIfNeeded();
	await shot("archive-buy-basis");
	const sell = orders.locator(".order-row").filter({ hasText: "卖出 4 股" });
	await sell.locator("summary").click();
	await expect(sell).toContainText("US$396.20");
	await expect(sell).toContainText("US$7.60");
	await expect(sell).toContainText("费用 US$0.20");
	await sell.scrollIntoViewIfNeeded();
	await shot("archive-sell-net-result");
	const note = archive.locator(".journal-list > article");
	await note.getByText("原计划与实际交易 · AAPL", { exact: true }).click();
	await expect(note).toContainText("US$396.20");
	await expect(note).toContainText("US$7.60");
	await expect(note).toContainText("US$120.00 / US$80.00");
	await expect(note).toContainText("controlled-test");
	await note.scrollIntoViewIfNeeded();
	await shot("archive-linked-plan");
	await expect(
		archive.getByRole("button", {
			name: /写复盘|申请撤单|模拟买入|模拟卖出|保存/,
		}),
	).toHaveCount(0);
	await expect(archive.locator("input, textarea, select")).toHaveCount(0);
	await page.setViewportSize({ width: 390, height: 844 });
	expect(await archive.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
		true,
	);
	expect(
		await page.evaluate(
			() => document.documentElement.scrollWidth <= innerWidth,
		),
	).toBe(true);
	await note.scrollIntoViewIfNeeded();
	await shot("archive-linked-mobile");
	expect(mutations).toEqual([]);
	page.off("request", track);

	// 显式受控旧接口响应：旧数据没有这些元信息，不能显示为零收益或猜测汇率。
	await page.route("**/api/rounds/*", async (route) => {
		const response = await route.fetch();
		const data = await response.json();
		if (!Array.isArray(data.orders)) return route.fulfill({ response });
		await route.fulfill({
			response,
			json: {
				...data,
				orders: data.orders.map((order) => ({
					...order,
					cost_basis: null,
					realized_pnl: null,
					source: "",
					fx_rate: "1",
					fx_source: "",
					fx_time: null,
					filled_at: null,
					confirmed_at: null,
					target_price: null,
					stop_price: null,
					fee: null,
				})),
			},
		});
	});
	await page.setViewportSize({ width: 1440, height: 1000 });
	await page.reload();
	await page.getByRole("button", { name: /个人账户/ }).click();
	await page.getByRole("button", { name: "查看历史" }).click();
	const legacy = page.getByRole("dialog", { name: "已归档练习 · 只读" });
	const oldSell = legacy
		.locator("section")
		.filter({ has: page.getByRole("heading", { name: /历史委托/ }) })
		.locator(".order-row")
		.filter({ hasText: "卖出 4 股" });
	await oldSell.locator("summary").click();
	await expect(oldSell).toContainText("历史值未记录");
	await expect(oldSell).toContainText("历史记录未注明");
	await expect(oldSell).toContainText("未记录 / 未记录");
	await expect(oldSell).not.toContainText("1 USD = 1 CNY");
	await expect(oldSell).not.toContainText("US$0.00");
	await oldSell.scrollIntoViewIfNeeded();
	await shot("archive-legacy-unknown");
	await page.unroute("**/api/rounds/*");
}
