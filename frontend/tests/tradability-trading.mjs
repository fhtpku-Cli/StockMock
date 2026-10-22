import { expect } from "@playwright/test";

export async function verifyTrading({
	page,
	engine,
	catalog,
	capture,
	open,
	discover,
	observations,
}) {
	let additions = 0;
	for (const [width, symbol, currency, min, step] of [
		[1280, "688981.SS", "CNY", 200, 1],
		[390, "IBM", "USD", 1, 1],
		[320, "512660.SS", "CNY", 100, 100],
	]) {
		await page.setViewportSize({ width, height: 900 });
		expect(catalog.some((i) => i.symbol === symbol)).toBe(false);
		await open(symbol);
		const dialog = page.getByRole("dialog");
		await expect(
			dialog.getByRole("button", { name: "模拟买入", exact: true }),
		).toBeDisabled();
		await dialog.getByRole("button", { name: "加入自选", exact: true }).click();
		await expect(
			dialog.getByRole("button", { name: "移除自选", exact: true }),
		).toBeEnabled();
		const endpoint = `**/api/instruments/${symbol}/enable`;
		if (symbol === "IBM") {
			await page.route(
				endpoint,
				(r) =>
					r.fulfill({
						status: 503,
						json: {
							detail: {
								code: "controlled_outage",
								message: "行情暂不可用，请稍后重试",
							},
						},
					}),
				{ times: 1 },
			);
			await dialog
				.getByRole("button", { name: "启用模拟交易", exact: true })
				.click();
			await expect(dialog.getByRole("alert")).toContainText("行情暂不可用");
			await expect(
				dialog.getByRole("button", { name: "模拟买入", exact: true }),
			).toBeDisabled();
			await capture(`${engine}-failure-observation-${width}`);
		}
		let count = 0,
			release;
		const pending = new Promise((done) => {
			release = done;
		});
		await page.route(endpoint, async (r) => {
			count++;
			await pending;
			await r.continue();
		});
		const requested = page.waitForRequest(endpoint);
		await dialog
			.getByRole("button", { name: /^(重试)?启用模拟交易$/ })
			.dblclick();
		await requested;
		await expect(
			dialog.getByRole("button", { name: "正在核验交易条件…" }),
		).toBeDisabled();
		await expect(
			dialog.getByRole("button", { name: "模拟买入", exact: true }),
		).toBeDisabled();
		await capture(`${engine}-pending-${width}`);
		const response = page.waitForResponse(endpoint, { timeout: 60000 });
		if (engine === "chromium" && width === 1280)
			await page.waitForTimeout(26000);
		release();
		const res = await response;
		expect(res.status()).toBe(200);
		const enabled = await res.json();
		expect(enabled.tradable).toBe(true);
		expect(enabled.min_buy_quantity).toBe(min);
		expect(enabled.quantity_step).toBe(step);
		await page.unroute(endpoint);
		expect(count).toBe(1);
		additions++;
		await expect(
			dialog.getByRole("button", { name: "模拟买入", exact: true }),
		).toBeEnabled();
		await expect(
			dialog.getByRole("button", { name: "模拟卖出", exact: true }),
		).toBeEnabled();
		await expect(
			dialog.getByRole("button", { name: /启用模拟交易/ }),
		).toHaveCount(0);
		await dialog.locator(".discovery-detail-actions").scrollIntoViewIfNeeded();
		await capture(`${engine}-enabled-${width}`);
		await dialog.getByRole("button", { name: "模拟买入", exact: true }).click();
		await expect(page.locator("#quantity")).toHaveValue(String(min));
		await expect(page.locator("#quantity")).toHaveAttribute(
			"step",
			String(step),
		);
		await page.getByLabel(`限价（${currency}）`).fill("100");
		const sizedResponse = page.waitForResponse("**/api/orders/size?percent=25");
		await page.getByRole("button", { name: "25%", exact: true }).click();
		const sized = await (await sizedResponse).json();
		expect(sized.suggested_quantity).toBeGreaterThanOrEqual(min);
		expect((sized.suggested_quantity - min) % step).toBe(0);
		await expect(page.locator("#quantity")).toHaveValue(
			String(sized.suggested_quantity),
		);
		if (min === 200) {
			await page.locator("#quantity").fill("201");
			expect(
				await page.locator("#quantity").evaluate((el) => el.checkValidity()),
			).toBe(true);
		}
		await page
			.getByRole("button", { name: "预览费用与资金占用", exact: true })
			.click();
		await expect(
			page.getByRole("heading", { name: "确认这笔委托" }),
		).toBeVisible();
		await capture(`${engine}-preview-${width}`);
		await page.keyboard.press("Escape");
		await expect(page.locator(`[data-symbol="${symbol}"]`)).toContainText(
			"可模拟交易",
		);
		await expect(
			page.locator(".watch-card").filter({ hasText: symbol }),
		).toContainText("可模拟交易");
		await page.getByLabel("搜索代码或名称").fill("");
		await expect(page.locator(`[data-symbol="${symbol}"]`)).toContainText(
			"可模拟交易",
		);
		await expect(page.locator(".security-row")).toHaveCount(
			catalog.length + additions,
		);
		await page.reload();
		await discover();
		await expect(
			page.locator(".watch-card").filter({ hasText: symbol }),
		).toContainText("可模拟交易");
		await open(symbol);
		await page
			.getByRole("dialog")
			.getByRole("button", { name: "模拟卖出", exact: true })
			.click();
		await expect(page.getByRole("heading", { name: /模拟卖出/ })).toBeVisible();
		await expect(page.locator("#quantity")).toBeEnabled();
		await capture(`${engine}-persisted-sell-${width}`);
		await page.keyboard.press("Escape");
		observations.push({
			scenario: `${engine}-${width}-启用与缓存刷新`,
			passed: true,
			symbol,
			requests: count,
			min,
			step,
			backendStatus: 200,
			sizing: sized,
			slowResponseDelayMs: engine === "chromium" && width === 1280 ? 26000 : 0,
		});
	}
	await open("VUG");
	let lostResponse;
	await page.route(
		"**/api/instruments/VUG/enable",
		async (route) => {
			const response = await route.fetch();
			lostResponse = await response.json();
			await route.abort("failed");
		},
		{ times: 1 },
	);
	await page.getByRole("button", { name: "启用模拟交易", exact: true }).click();
	await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
		"暂时无法连接服务",
	);
	expect(lostResponse.tradable).toBe(true);
	await expect(
		page
			.getByRole("dialog")
			.getByRole("button", { name: "模拟买入", exact: true }),
	).toBeDisabled();
	await capture(`${engine}-lost-response-observation`);

	await page
		.getByRole("button", { name: "重试启用模拟交易", exact: true })
		.click();
	await expect(
		page
			.getByRole("dialog")
			.getByRole("button", { name: "模拟买入", exact: true }),
	).toBeEnabled();
	await capture(`${engine}-us-etf-enabled`);
	await page.keyboard.press("Escape");
}
