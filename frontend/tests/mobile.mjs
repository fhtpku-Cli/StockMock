import { mkdir, readFile, writeFile } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { devices, expect } from "@playwright/test";
import { launchBrowser } from "./browser.mjs";

const output = new URL("../../.omo/evidence/mobile-ui/", import.meta.url)
	.pathname;
await mkdir(output, { recursive: true });
const password = (
	await readFile(
		new URL("../../backend/.local-password", import.meta.url),
		"utf8",
	)
).trim();
const browser = await launchBrowser();
const context = await browser.newContext({
	...devices["iPhone 13"],
	viewport: { width: 390, height: 844 },
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [],
	observations = [],
	audits = [];
page.on("pageerror", (e) => errors.push(e.message));
const shot = async (name) => {
	await page.screenshot({
		path: output + name + ".png",
		animations: "disabled",
	});
	observations.push({ name, text: await page.locator("body").innerText() });
};
const login = async (target) => {
	await target.goto("http://127.0.0.1:8001");
	await target.getByLabel("访问密码").fill(password);
	await target.getByRole("button", { name: "进入我的空间" }).click();
	await expect(target.getByRole("heading", { name: "资产概览" })).toBeVisible();
};
try {
	await login(page);
	expect(
		await page.locator('meta[name="viewport"]').getAttribute("content"),
	).toContain("viewport-fit=cover");
	const icon = await context.request.get(
		"http://127.0.0.1:8001/apple-touch-icon.png",
	);
	expect(icon.status()).toBe(200);
	expect((await icon.body()).readUInt32BE(16)).toBe(180);
	await shot("assets-phone");
	for (const [width, height] of [
		[375, 667],
		[430, 932],
		[844, 390],
	]) {
		await page.setViewportSize({ width, height });
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth > innerWidth,
			),
		).toBe(false);
		await shot(`assets-${width}`);
	}
	await page.setViewportSize({ width: 390, height: 844 });
	await page.getByRole("button", { name: "自选", exact: true }).tap();
	await page.getByLabel("搜索代码或名称").fill("600519");
	await page.locator('[data-symbol="600519.SS"] .security-main').tap();
	await page.getByRole("button", { name: "模拟买入", exact: true }).tap();
	await page.getByLabel("限价（CNY）").fill("100");
	await page.getByLabel("数量（每手 100）").fill("9900");
	await page
		.getByLabel("这次交易的理由")
		.fill("受控手机验收：丢失响应后重试不重复受理。");
	expect(
		await page
			.getByLabel("限价（CNY）")
			.evaluate((el) => Number.parseFloat(getComputedStyle(el).fontSize)),
	).toBeGreaterThanOrEqual(16);
	await page.setViewportSize({ width: 390, height: 420 });
	await page
		.getByRole("button", { name: "预览费用与资金占用" })
		.scrollIntoViewIfNeeded();
	const bounds = await page.getByRole("dialog").boundingBox();
	expect(bounds.y).toBeGreaterThanOrEqual(0);
	expect(bounds.y + bounds.height).toBeLessThanOrEqual(421);
	await shot("trade-short-viewport");
	await page.getByRole("button", { name: "预览费用与资金占用" }).tap();
	await expect(
		page.getByRole("heading", { name: "确认这笔委托" }),
	).toBeVisible();
	await page.context().setOffline(true);
	await expect(
		page.getByRole("button", { name: "预览费用与资金占用" }),
	).toBeDisabled();
	await shot("trade-offline");
	await page.context().setOffline(false);
	await expect(page.getByRole("button", { name: "确认提交委托" })).toHaveCount(
		0,
	);
	await page.setViewportSize({ width: 390, height: 844 });
	await page.getByRole("button", { name: "预览费用与资金占用" }).tap();
	await expect(
		page.getByRole("heading", { name: "确认这笔委托" }),
	).toBeVisible();
	const submissions = [];
	let dropped = false;
	await page.route("**/api/orders", async (route) => {
		if (route.request().method() !== "POST") {
			await route.continue();
			return;
		}
		submissions.push(route.request().postDataJSON());
		if (!dropped) {
			dropped = true;
			const response = await route.fetch();
			expect(response.status()).toBe(200);
			await route.abort("failed");
		} else await route.continue();
	});
	await page.getByRole("button", { name: "确认提交委托" }).tap();
	await expect(page.getByRole("alert")).toContainText("无法连接");
	await shot("lost-response");
	await page.evaluate(() =>
		document.dispatchEvent(new Event("visibilitychange")),
	);
	await context.setOffline(true);
	await expect(
		page.getByRole("button", { name: "重试确认委托结果" }),
	).toBeDisabled();
	await context.setOffline(false);
	await expect(page.getByLabel("数量（每手 100）")).toBeDisabled();
	await expect(
		page.getByRole("button", { name: "预览费用与资金占用" }),
	).toHaveCount(0);
	await page.getByRole("button", { name: "重试确认委托结果" }).tap();
	await expect(page.locator(".order-row")).toHaveCount(1);
	expect(submissions).toHaveLength(2);
	expect(submissions[0]).toEqual(submissions[1]);
	const ledgerOrders = await (
		await context.request.get("http://127.0.0.1:8001/api/orders")
	).json();
	expect(ledgerOrders).toHaveLength(1);
	await shot("single-order-after-retry");
	const desktop = await browser.newContext({
		viewport: { width: 1440, height: 1000 },
	});
	const second = await desktop.newPage();
	await login(second);
	await second.getByRole("button", { name: "委托", exact: true }).click();
	await expect(second.locator(".order-row")).toHaveCount(1);
	await second.screenshot({
		path: output + "same-account-desktop.png",
		animations: "disabled",
	});
	await page.getByRole("button", { name: "申请撤单" }).tap();
	await expect(page.locator(".order-row")).toContainText("已撤单");
	await second.getByRole("button", { name: "刷新数据", exact: true }).click();
	await expect(second.locator(".order-row")).toContainText("已撤单");
	await desktop.close();
	await context.clearCookies();
	await page.getByRole("button", { name: "刷新数据", exact: true }).tap();
	await expect(page.getByLabel("访问密码")).toBeVisible();
	await expect(page.locator(".order-row")).toHaveCount(0);
	await expect(page.getByRole("dialog")).toHaveCount(0);
	await shot("session-expired");
	await page.getByLabel("访问密码").fill(password);
	await page.getByRole("button", { name: "进入我的空间" }).tap();
	await expect(page.getByRole("heading", { name: "委托记录" })).toBeVisible();
	await expect(page.locator(".order-row")).toHaveCount(1);
	await shot("session-restored");
	audits.push({
		surface: "orders",
		violations: (await new AxeBuilder({ page }).analyze()).violations,
	});
	await page.getByRole("button", { name: "账户设置", exact: true }).tap();
	await shot("settings-phone");
	audits.push({
		surface: "settings",
		violations: (await new AxeBuilder({ page }).analyze()).violations,
	});
	await page.getByRole("button", { name: "关闭" }).tap();
	await page.getByRole("button", { name: "自选", exact: true }).tap();
	await page.getByLabel("搜索代码或名称").fill("600519");
	await page.locator('[data-symbol="600519.SS"] .security-main').tap();
	await page.getByRole("button", { name: "模拟买入", exact: true }).tap();
	await page.getByLabel("限价（CNY）").fill("100");
	await page.getByRole("button", { name: "预览费用与资金占用" }).tap();
	await expect(
		page.getByRole("button", { name: "确认提交委托" }),
	).toBeVisible();
	const concurrent = await context.request.post(
		"http://127.0.0.1:8001/api/orders",
		{
			headers: { Origin: "http://127.0.0.1:8001" },
			data: { ...submissions[0], request_id: crypto.randomUUID() },
		},
	);
	expect(concurrent.status()).toBe(200);
	const rejected = page.waitForResponse(
		(r) => r.url().endsWith("/api/orders") && r.request().method() === "POST",
	);
	await page.getByRole("button", { name: "确认提交委托" }).tap();
	expect((await rejected).status()).toBeGreaterThanOrEqual(400);
	await expect(page.getByLabel("数量（每手 100）")).toBeEnabled();
	await expect(
		page.getByRole("button", { name: "预览费用与资金占用" }),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: "重试确认委托结果" }),
	).toHaveCount(0);
	await shot("definite-rejection-unlocks-form");
	for (const audit of audits) expect(audit.violations).toEqual([]);
	expect(errors).toEqual([]);
	await writeFile(
		output + "controlled.json",
		JSON.stringify(
			{
				source: "受控行情、临时SQLite、真实API；Chromium手机仿真，非真机",
				observations,
				errors,
				audits,
				submissions: submissions.length,
				reconciliationSubmissions: 2,
				definiteRejections: 1,
				reconciliationRequestIds: new Set(
					submissions.slice(0, 2).map((x) => x.request_id),
				).size,
				persistedOrdersAfterReconciliation: ledgerOrders.length,
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({
			scenarios: observations.map((o) => o.name),
			errors,
			axe: audits.map((a) => [a.surface, a.violations.length]),
			submissions: submissions.length,
			reconciliationSubmissions: 2,
			definiteRejections: 1,
			persistedOrdersAfterReconciliation: ledgerOrders.length,
		}),
	);
} catch (error) {
	await shot("failure");
	throw error;
} finally {
	await browser.close();
}
