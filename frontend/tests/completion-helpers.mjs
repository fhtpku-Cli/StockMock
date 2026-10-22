import { verifyPreview } from "./completion-checks.mjs";
import { launchBrowser } from "./browser.mjs";
import { expect } from "@playwright/test";
import { readFile, writeFile, mkdir } from "node:fs/promises";
export async function createCompletionFixture() {
	const out = new URL(
		process.env.FRONTEND_TEST_OUTPUT ??
			"../../.omo/evidence/completion-20261023/ui/",
		import.meta.url,
	).pathname;
	await mkdir(out, { recursive: true });
	const url = process.env.FRONTEND_TEST_URL ?? "http://127.0.0.1:8004";
	const browser = await launchBrowser();
	const context = await browser.newContext({
		viewport: { width: 1440, height: 1000 },
	});
	const page = await context.newPage();
	page.setDefaultTimeout(15000);
	const errors = [],
		scenes = [];
	page.on("pageerror", (e) => errors.push(e.message));
	const read = async (path) => (await context.request.get(url + path)).json();
	const shot = async (name) => {
		await page.screenshot({
			path: out + name + ".png",
			animations: "disabled",
			fullPage: true,
		});
		scenes.push({ name, text: await page.locator("body").innerText() });
	};
	const tick = async (symbol, price, fx_available = true) => {
		const sequence = crypto.randomUUID();
		await writeFile(
			out + "control.json",
			JSON.stringify({ sequence, symbol, price, fx_available }),
		);
		await expect
			.poll(async () => {
				try {
					return JSON.parse(await readFile(out + "ack.json", "utf8"));
				} catch {
					return null;
				}
			})
			.toMatchObject({ sequence, ok: true });
		await page.getByRole("button", { name: "刷新数据", exact: true }).click();
	};
	const discover = async (symbol) => {
		await page.getByRole("button", { name: "自选", exact: true }).click();
		await page.getByLabel("筛选市场").selectOption("all");
		await page.getByLabel("筛选证券类型").selectOption("all");
		await page.getByLabel("筛选交易状态").selectOption("all");
		await page.getByLabel("搜索代码或名称").fill(symbol);
		await page.locator(".security-main").click();
	};
	let previewCount = 0;
	const order = async (side, quantity, price) => {
		await discover("AAPL");
		await page
			.getByRole("button", {
				name: side === "buy" ? "模拟买入" : "模拟卖出",
				exact: true,
			})
			.click();
		await page.getByLabel(/数量（每手/).fill(String(quantity));
		await page.getByLabel("限价（USD）").fill(String(price));
		await page
			.getByLabel("这次交易的理由")
			.fill(
				side === "buy"
					? "受控验收：计划持有两周，检验资金与成本。"
					: "受控验收：分批退出并复盘费用。",
			);
		await page.getByText("记录目标价与止损参考价", { exact: true }).click();
		await page.getByLabel("目标价", { exact: true }).fill("120");
		await page.getByLabel("止损参考价", { exact: true }).fill("80");
		const response = page.waitForResponse(
			(r) =>
				r.url().endsWith("/api/orders/preview") &&
				r.request().method() === "POST",
		);
		await page.getByRole("button", { name: "预览费用与资金占用" }).click();
		await expect(
			page.getByRole("heading", { name: "确认这笔委托" }),
		).toBeVisible();
		const preview = await (await response).json();
		await verifyPreview({ page, preview, side });
		await shot(`preview-${++previewCount}-${side}`);
		scenes.push({ name: `preview-api-${previewCount}`, preview });
		await page.setViewportSize({ width: 1440, height: 1000 });
		await page.getByRole("button", { name: "确认提交委托" }).click();
		await expect(page.getByRole("heading", { name: "委托记录" })).toBeVisible();
	};
	return {
		out,
		url,
		browser,
		context,
		page,
		errors,
		scenes,
		read,
		shot,
		tick,
		order,
	};
}
