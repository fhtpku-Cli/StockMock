import { resolve } from "node:path";
import { expect } from "@playwright/test";

export function uiHelpers(getPage, output, observations) {
	return {
		async capture(name) {
			const page = getPage();
			await page.screenshot({ path: resolve(output, `${name}.png`) });
			const overflow = await page.evaluate(() =>
				[...document.querySelectorAll(".modal,.main-scroll")].some(
					(el) => el.scrollWidth > el.clientWidth + 1,
				),
			);
			expect(overflow).toBe(false);
			await expect(page.locator("vite-error-overlay")).toHaveCount(0);
			observations.push({
				scenario: name,
				passed: true,
				overflow,
				artifact: `${name}.png`,
				text: await page.locator("body").innerText(),
			});
		},
		async discover() {
			const page = getPage();
			await page.getByRole("button", { name: "自选", exact: true }).click();
			await expect(page.getByLabel("搜索代码或名称")).toBeVisible();
		},
		async open(symbol) {
			const page = getPage();
			await page.getByLabel("搜索代码或名称").fill(symbol.split(".")[0]);
			const row = page.locator(`[data-symbol="${symbol}"]`);
			await expect(row).toBeVisible();
			await row.locator(".security-main").click();
			await expect(
				page.getByRole("dialog").locator(".quote-price"),
			).toBeVisible();
		},
	};
}

export async function stopServer(child) {
	if (!child || child.exitCode !== null || child.signalCode !== null) return;
	child.kill("SIGTERM");
	await new Promise((done) => child.once("exit", done));
}
