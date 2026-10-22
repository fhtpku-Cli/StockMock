import { readFile, writeFile } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { chromium, devices, expect } from "@playwright/test";
import { baseUrl, launchBrowser } from "./browser.mjs";

const output = new URL("../../.omo/evidence/frontend/", import.meta.url)
	.pathname;
const browser = await launchBrowser();
const context = await browser.newContext({
	viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(30000);
const observations = [];
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const capture = async (name) => {
	await page.screenshot({
		animations: "disabled",
		path: output + name + ".png",
		fullPage: true,
	});
	observations.push({
		name,
		text: await page.locator("body").innerText(),
		overflow: await page.evaluate(
			() => document.documentElement.scrollWidth > innerWidth,
		),
	});
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
	await page.getByRole("heading", { name: "资产概览" }).waitFor();
	await page.getByRole("button", { name: "自选", exact: true }).click();
	await page
		.getByRole("button", { name: "添加自选 苹果", exact: true })
		.click();
	await page
		.getByRole("button", { name: "移除自选 苹果", exact: true })
		.waitFor();
	await capture("watchlist-added");
	await page.getByLabel("搜索代码或名称").fill("AAPL");
	await expect(page.locator('[data-symbol="AAPL"]')).toHaveCount(1);
	await capture("search-filter");
	await page.locator('[data-symbol="AAPL"] .security-main').click();
	await page.getByRole("dialog").waitFor();
	await expect(
		page.getByRole("button", { name: "模拟买入", exact: true }),
	).toBeDisabled();
	await expect(page.getByRole("dialog").locator(".loading")).toHaveCount(0, {
		timeout: 60000,
	});
	await capture("detail-observe");
	const detailAxe = await new AxeBuilder({ page }).analyze();
	await page.keyboard.press("Escape");
	await expect(page.getByRole("dialog")).toHaveCount(0);
	await page
		.getByRole("button", { name: "移除自选 苹果", exact: true })
		.click();
	await page
		.getByRole("button", { name: "添加自选 苹果", exact: true })
		.waitFor();
	await page.getByRole("button", { name: "复盘", exact: true }).click();
	const note = "界面联调记录：先记录判断，再观察结果。此条为本地验收写入。";
	await page.getByLabel("复盘内容").fill(note);
	await page.getByRole("button", { name: "保存记录", exact: true }).click();
	await page.getByText("已保存到本轮复盘。", { exact: true }).waitFor();
	await expect(page.locator(".journal-list")).toContainText(note);
	await page.reload();
	await page.getByRole("button", { name: "复盘", exact: true }).click();
	await expect(page.locator(".journal-list")).toContainText(note);
	await capture("journal-persisted");
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await page.getByRole("button", { name: "管理", exact: true }).click();
	await expect(page.getByRole("dialog").locator(".loading")).toHaveCount(0, {
		timeout: 60000,
	});
	await capture("cash-fx");
	await page.getByLabel("换回金额").fill("1");
	if (await page.getByRole("button", { name: "确认换回人民币" }).isEnabled()) {
		await page.getByRole("button", { name: "确认换回人民币" }).click();
		await page.getByRole("alert").waitFor();
		await capture("fx-insufficient-funds");
	}
	await page.keyboard.press("Escape");
	await page.getByRole("button", { name: /个人账户/ }).click();
	await capture("settings");
	await expect(
		page.getByRole("button", { name: "归档本轮，开始新练习" }),
	).toBeDisabled();
	await page.keyboard.press("Escape");
	await page.getByRole("button", { name: "委托", exact: true }).click();
	const dl = page.waitForEvent("download");
	await page.getByRole("link", { name: "导出流水" }).click();
	const download = await dl;
	await download.saveAs(output + "ledger-export.csv");
	const mobile = await browser.newContext({
		...devices["iPhone 14"],
		reducedMotion: "reduce",
		storageState: await context.storageState(),
	});
	const m = await mobile.newPage();
	await m.goto(baseUrl);
	await m.locator(".asset-value").waitFor();
	const mobileResults = [];
	for (const [label, name] of [
		["资产", "assets"],
		["自选", "watchlist"],
		["委托", "orders"],
		["复盘", "journal"],
	]) {
		await m.getByRole("button", { name: label, exact: true }).click();
		await m.waitForTimeout(400);
		await m.screenshot({
			animations: "disabled",
			path: output + name + "-phone.png",
		});
		await m
			.locator(".main-scroll")
			.evaluate((e) => e.scrollTo(0, e.scrollHeight));
		await m.screenshot({
			animations: "disabled",
			path: output + name + "-phone-end.png",
		});
		mobileResults.push({
			name,
			overflow: await m.evaluate(
				() => document.documentElement.scrollWidth > innerWidth,
			),
			audit: (await new AxeBuilder({ page: m }).analyze()).violations.map(
				(v) => ({
					id: v.id,
					impact: v.impact,
					nodes: v.nodes.map((n) => n.target),
				}),
			),
		});
	}
	await m.getByRole("button", { name: "账户设置" }).click();
	await m.screenshot({
		animations: "disabled",
		path: output + "settings-phone.png",
	});
	await m.keyboard.press("Escape");
	await mobile.setOffline(true);
	await expect(m.getByRole("alert")).toContainText("当前离线");
	await m.screenshot({
		animations: "disabled",
		path: output + "offline-phone.png",
	});
	await mobile.close();
	await writeFile(
		output + "interactions.json",
		JSON.stringify(
			{
				observations,
				errors,
				detailAxe: detailAxe.violations,
				mobileResults,
				source: "真实后端与真实外部行情请求；复盘写入明确标记为本地验收数据",
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify(
			{
				errors,
				detailAxe: detailAxe.violations.map((v) => v.id),
				mobileResults,
			},
			null,
			2,
		),
	);
} catch (error) {
	await capture("interaction-failure");
	throw error;
} finally {
	await browser.close();
}
