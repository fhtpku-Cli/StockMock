import { launchBrowser, baseUrl } from "./browser.mjs";
import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFile, mkdir, writeFile } from "node:fs/promises";
const evidence = new URL("../../.omo/evidence/frontend/", import.meta.url)
	.pathname;
await mkdir(evidence, { recursive: true });
const browser = await launchBrowser();
const context = await browser.newContext({
	viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
	await page.goto(baseUrl);
	await page.getByLabel("访问密码").waitFor();
	await page.screenshot({
		animations: "disabled",
		path: evidence + "login-desktop.png",
		fullPage: true,
	});
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
	await page
		.locator(".asset-value")
		.filter({ hasText: "1,000,000.00" })
		.waitFor();
	await page.screenshot({
		animations: "disabled",
		path: evidence + "assets-desktop.png",
		fullPage: true,
	});
	const results = {
		errors,
		assets: await page.locator("main").innerText(),
		audit: await new AxeBuilder({ page }).analyze(),
	};
	for (const label of ["自选", "委托", "复盘"]) {
		await page.getByRole("button", { name: label, exact: true }).click();
		await page.waitForTimeout(600);
		await page.screenshot({
			animations: "disabled",
			path:
				evidence +
				{ 自选: "watchlist", 委托: "orders", 复盘: "journal" }[label] +
				"-desktop.png",
			fullPage: true,
		});
		results[label] = await page.locator("main").innerText();
	}
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await page.setViewportSize({ width: 390, height: 844 });
	await page.screenshot({
		animations: "disabled",
		path: evidence + "assets-mobile.png",
		fullPage: true,
	});
	results.mobileOverflow = await page.evaluate(
		() => document.documentElement.scrollWidth > window.innerWidth,
	);
	await writeFile(evidence + "smoke.json", JSON.stringify(results, null, 2));
	console.log(
		JSON.stringify(
			{
				errors,
				violations: results.audit.violations.map((v) => ({
					id: v.id,
					impact: v.impact,
					nodes: v.nodes.map((n) => ({
						target: n.target,
						summary: n.failureSummary,
					})),
				})),
				mobileOverflow: results.mobileOverflow,
				views: Object.fromEntries(
					["自选", "委托", "复盘"].map((k) => [k, results[k]]),
				),
			},
			null,
			2,
		),
	);
} catch (error) {
	await page.screenshot({
		animations: "disabled",
		path: evidence + "failure.png",
		fullPage: true,
	});
	console.log(await page.locator("body").innerText());
	throw error;
} finally {
	await browser.close();
}
