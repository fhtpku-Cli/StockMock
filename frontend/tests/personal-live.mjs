import { launchBrowser, baseUrl } from "./browser.mjs";
import { expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFile, writeFile } from "node:fs/promises";
const output = new URL(
	"../../.omo/evidence/frontend-personal/",
	import.meta.url,
).pathname;
const browser = await launchBrowser();
const context = await browser.newContext({
	viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(20000);
const errors = [],
	observations = [];
page.on("pageerror", (e) => errors.push(e.message));
const shot = async (name) => {
	await page.screenshot({
		path: output + name + ".png",
		fullPage: true,
		animations: "disabled",
	});
	observations.push({ name, text: await page.locator("body").innerText() });
};
try {
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
	await expect(page.getByRole("heading", { name: "资产概览" })).toBeVisible();
	await expect(page.locator(".asset-value")).not.toHaveText("—");
	await shot("live-assets-desktop");
	await page.getByRole("button", { name: "自选", exact: true }).click();
	await expect(page.locator(".security-row").first()).toBeVisible();
	const count = await page.locator(".security-row").count();
	expect(count).toBeGreaterThan(8);
	await page.getByLabel("筛选市场").selectOption("US");
	await page.getByLabel("筛选证券类型").selectOption("ETF");
	await expect(page.locator(".security-row").first()).toContainText("ETF");
	await shot("live-us-etf-catalog");
	await page.getByLabel("筛选证券类型").selectOption("all");
	await page.getByLabel("筛选市场").selectOption("HK");
	await page.getByLabel("筛选交易状态").selectOption("observe");
	await expect(page.locator(".security-row").first()).toContainText("仅观察");
	await shot("live-hk-restriction");
	await page.setViewportSize({ width: 390, height: 844 });
	await shot("live-catalog-phone");
	await page.getByRole("button", { name: "委托", exact: true }).click();
	await expect(page.getByLabel("关联市场")).toBeVisible();
	await shot("live-orders-phone");
	await page.getByRole("button", { name: "复盘", exact: true }).click();
	await expect(page.getByLabel("起始日期")).toBeVisible();
	await shot("live-journal-phone");
	await page.getByRole("button", { name: "账户设置", exact: true }).click();
	await expect(page.getByRole("dialog")).toContainText("添加到主屏幕");
	await shot("live-settings-phone");
	const axe = (await new AxeBuilder({ page }).analyze()).violations;
	expect(axe).toEqual([]);
	expect(errors).toEqual([]);
	await writeFile(
		output + "live.json",
		JSON.stringify(
			{ url: baseUrl, catalogCount: count, errors, axe, observations },
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({
			catalogCount: count,
			scenarios: observations.map((o) => o.name),
			errors,
			axe: axe.length,
		}),
	);
} catch (error) {
	await shot("live-failure");
	throw error;
} finally {
	await browser.close();
}
