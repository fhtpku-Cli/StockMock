import { launchBrowser } from "./browser.mjs";
import { expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFile, writeFile, mkdir } from "node:fs/promises";
const output = new URL(
	"../../.omo/evidence/frontend-personal/",
	import.meta.url,
).pathname;
await mkdir(output, { recursive: true });
const browser = await launchBrowser();
const context = await browser.newContext({
	viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(12000);
const observations = [];
const errors = [];
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
	await page.goto("http://127.0.0.1:8002");
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
	await expect(page.getByTestId("asset-segment")).toHaveCount(3);
	await page
		.getByRole("heading", { name: "资产轨迹" })
		.scrollIntoViewIfNeeded();
	await page
		.getByRole("img", { name: "每日资产轨迹，缺失日期断开" })
		.scrollIntoViewIfNeeded();
	await shot("daily-history-desktop");
	await page.getByRole("button", { name: "自选", exact: true }).click();
	await page.getByLabel("搜索代码或名称").fill("510300");
	await page.locator(".security-main").click();
	await page.getByRole("button", { name: "模拟买入", exact: true }).click();
	await page.getByLabel("限价（CNY）").fill("100");
	const sizeReply = page.waitForResponse(
		(r) =>
			r.url().includes("orders/size?percent=25") &&
			r.request().method() === "POST",
	);
	await page.getByRole("button", { name: "25%", exact: true }).click();
	const sized = await (await sizeReply).json();
	expect(sized.suggested_quantity).toBeGreaterThan(0);
	await expect(page.getByLabel(/数量（每手/)).toHaveValue(
		String(sized.suggested_quantity),
	);
	await expect(page.getByRole("button", { name: "确认提交委托" })).toHaveCount(
		0,
	);
	await page.getByRole("button", { name: "预览费用与资金占用" }).click();
	await expect(
		page.getByRole("heading", { name: "确认这笔委托" }),
	).toBeVisible();
	await shot("server-sized-preview");
	await page.context().setOffline(true);
	await expect(
		page.getByRole("button", { name: "预览费用与资金占用" }),
	).toBeDisabled();
	await page.context().setOffline(false);
	await expect(page.getByRole("button", { name: "确认提交委托" })).toHaveCount(
		0,
	);
	await page.keyboard.press("Escape");
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await page.setViewportSize({ width: 390, height: 844 });
	await page
		.getByRole("img", { name: "每日资产轨迹，缺失日期断开" })
		.scrollIntoViewIfNeeded();
	await shot("daily-history-phone");
	await expect(page.locator("body")).not.toHaveClass(/error-overlay/);
	await page.getByRole("button", { name: "账户设置", exact: true }).click();
	await page.getByRole("button", { name: "分红与拆股", exact: true }).click();
	const dialog = page.getByRole("dialog", {
		name: "人工登记分红与拆股",
		exact: true,
	});
	await dialog.getByLabel("当前持仓").selectOption("600519.SS");
	await dialog.getByLabel("生效日期").fill("2026-10-22");
	await dialog.getByLabel(/税前每股股息/).fill("1");
	await dialog.getByLabel("收款状态").selectOption("receivable");
	await dialog
		.getByLabel("公告来源与核对依据")
		.fill("受控测试公告：税前每股股息一元，先登记应收。");
	await expect(
		dialog.getByRole("button", { name: "确认登记公司行动" }),
	).toBeDisabled();
	await dialog.getByRole("checkbox").check();
	await expect(
		dialog.getByRole("button", { name: "确认登记公司行动" }),
	).toBeEnabled();
	await shot("dividend-confirm-phone");
	await dialog.getByRole("button", { name: "确认登记公司行动" }).click();
	await expect(dialog.getByRole("status")).toContainText("调整已登记");
	await dialog.getByRole("button", { name: "登记收到股息" }).click();
	await page.getByRole("button", { name: "确认收款并记账" }).click();
	await expect(
		dialog.getByRole("button", { name: "登记收到股息" }),
	).toHaveCount(0);
	await expect(dialog.locator(".order-row")).toContainText("¥100.00");
	await shot("dividend-paid-phone");
	await dialog.getByLabel("调整类型").selectOption("split");
	await dialog.getByLabel("调整后份数").fill("2");
	await dialog.getByLabel("对应调整前份数").fill("3");
	await dialog.getByRole("checkbox").check();
	await dialog.getByRole("button", { name: "确认登记公司行动" }).click();
	await expect(dialog.getByRole("alert")).toContainText("碎股");
	await shot("fractional-split-rejected");
	await dialog.getByLabel("对应调整前份数").fill("1");
	await expect(
		dialog.getByRole("button", { name: "确认登记公司行动" }),
	).toBeDisabled();
	await dialog.getByRole("checkbox").check();
	await dialog.getByRole("button", { name: "确认登记公司行动" }).click();
	await expect(dialog.getByRole("status")).toContainText("调整已登记");
	await expect(dialog.locator(".order-row").last()).toContainText("100 → 200");
	await page.setViewportSize({ width: 1440, height: 1000 });
	await shot("split-record-desktop");
	const axe = (await new AxeBuilder({ page }).analyze()).violations;
	if (axe.length)
		throw new Error(
			JSON.stringify(
				axe.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target) })),
			),
		);
	await page.keyboard.press("Escape");
	await page.keyboard.press("Escape");
	await page.getByRole("button", { name: "自选", exact: true }).click();
	await page.getByLabel("筛选市场").selectOption("CN");
	await page.getByLabel("筛选交易状态").selectOption("tradable");
	await page.getByLabel("搜索代码或名称").fill("600519");
	await expect(page.locator(".security-row")).toHaveCount(1);
	await shot("catalog-filters-desktop");
	await page.setViewportSize({ width: 390, height: 844 });
	await shot("catalog-filters-phone");
	await page.setViewportSize({ width: 1440, height: 1000 });
	await page.locator(".security-main").click();
	await page.getByRole("button", { name: "模拟买入", exact: true }).click();
	await page.getByLabel("限价（CNY）").fill("100");
	await page.context().setOffline(true);
	await expect(
		page.getByRole("button", { name: "预览费用与资金占用" }),
	).toBeDisabled();
	await shot("trade-offline-blocked");
	await page.context().setOffline(false);
	await page.keyboard.press("Escape");
	await page.getByRole("button", { name: "复盘", exact: true }).click();
	await page.getByLabel("起始日期").fill("2026-10-22");
	await page.getByLabel("结束日期").fill("2026-10-22");
	await page.getByLabel("关联市场").selectOption("CN");
	await shot("records-filters-desktop");
	await page.setViewportSize({ width: 390, height: 844 });
	await shot("records-filters-phone");
	await page.getByRole("button", { name: "账户设置", exact: true }).click();
	await page.getByLabel("输入“重新开始”以确认").fill("重新开始");
	await page.getByRole("button", { name: "归档本轮，开始新练习" }).click();
	await expect(page.getByRole("dialog")).toHaveCount(0);
	await page.getByRole("button", { name: "账户设置", exact: true }).click();
	await page.getByRole("button", { name: "查看历史", exact: true }).click();
	const archive = page.getByRole("dialog", { name: "已归档练习 · 只读" });
	await expect(archive).toContainText("100 → 200");
	await expect(archive.getByTestId("asset-segment")).toHaveCount(3);
	await expect(
		archive.getByRole("button", { name: "登记收到股息" }),
	).toHaveCount(0);
	await shot("personal-archive-phone");
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth > innerWidth,
	);
	expect(overflow).toBe(false);
	expect(errors).toEqual([]);
	await writeFile(
		output + "personal.json",
		JSON.stringify(
			{
				source: "隔离受控行情与种子持仓、真实API和临时SQLite",
				observations,
				errors,
				axe,
				overflow,
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({
			scenarios: observations.map((o) => o.name),
			errors,
			axe: axe.length,
			overflow,
		}),
	);
} catch (error) {
	await shot("personal-failure");
	throw error;
} finally {
	await browser.close();
}
