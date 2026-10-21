import { expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { launchBrowser, baseUrl } from "./browser.mjs";
const output = new URL("../../.omo/evidence/frontend/", import.meta.url)
	.pathname;
const browser = await launchBrowser();
const context = await browser.newContext({
	viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(30000);
const evidence = {
	source: "真实行情、真实服务、盘前挂单后立即撤单；不验证实时成交",
	steps: [],
};
const capture = async (name) => {
	await page.screenshot({
		animations: "disabled",
		path: output + name + ".png",
		fullPage: true,
	});
	evidence.steps.push({ name, text: await page.locator("body").innerText() });
};
try {
	const hour = Number(
		new Intl.DateTimeFormat("en-GB", {
			timeZone: "Asia/Shanghai",
			hour: "2-digit",
			hour12: false,
		}).format(new Date()),
	);
	if (hour >= 9 && hour < 16)
		throw new Error(
			"本验收脚本仅允许中国市场盘前或盘后运行，避免测试委托产生非预期模拟成交。",
		);
	await page.goto(baseUrl);
	await page
		.getByLabel("访问密码")
		.fill(
			(
				await readFile(
					new URL("../../backend/.local-password", import.meta.url),
					"utf8",
				)
			).trim(),
		);
	await page.getByRole("button", { name: "进入我的空间" }).click();
	await page.getByRole("heading", { name: "资产概览" }).waitFor();
	await page.getByRole("button", { name: "自选", exact: true }).click();
	const row = page.locator(".security-row").filter({ hasText: "平安银行" });
	await expect(row).toContainText("可交易");
	await row.locator(".security-main").click();
	await expect(
		page.getByRole("button", { name: "模拟买入", exact: true }),
	).toBeEnabled();
	const quoteResponse = await page.request.get(
		baseUrl + "/api/instruments/000001.SZ/quote",
	);
	const quote = await quoteResponse.json();
	evidence.quote = quote;
	await page.getByRole("button", { name: "模拟买入", exact: true }).click();
	await page.getByLabel("限价（CNY）").fill(quote.price);
	await page
		.getByLabel("这次交易的理由")
		.fill("本地验收：真实行情盘前预挂后立即撤单，不代表投资决策。");
	await page.getByRole("button", { name: "预览费用与资金占用" }).click();
	await page.getByRole("heading", { name: "确认这笔委托" }).waitFor();
	await capture("live-cn-preview");
	await page.getByRole("button", { name: "确认提交委托" }).click();
	const order = page
		.locator(".order-row")
		.filter({ hasText: "000001.SZ" })
		.first();
	await expect(order).toContainText("等待成交");
	await capture("live-cn-accepted");
	await order.getByRole("button", { name: "申请撤单" }).click();
	await expect(order).toContainText("已撤单");
	await capture("live-cn-cancelled");
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await expect(page.locator(".metric").nth(1)).toContainText("¥0.00");
	await expect(page.locator(".asset-value")).toContainText("1,000,000.00");
	await capture("live-cn-released");
	evidence.result = "通过";
	await writeFile(
		output + "live-order.json",
		JSON.stringify(evidence, null, 2),
	);
	console.log("真实盘前预览、受理、撤单、释放资金：通过");
} catch (error) {
	evidence.result = error.message;
	await capture("live-cn-failure");
	await writeFile(
		output + "live-order.json",
		JSON.stringify(evidence, null, 2),
	);
	throw error;
} finally {
	await browser.close();
}
