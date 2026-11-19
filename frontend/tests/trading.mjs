import { acceptCancelDialog } from "./cancel-dialog.mjs";
import { launchBrowser, baseUrl } from "./browser.mjs";
import { chromium, expect, devices } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, readFile, writeFile } from "node:fs/promises";
const output = new URL(
	process.env.FRONTEND_TEST_OUTPUT ?? "../../.omo/evidence/frontend/",
	import.meta.url,
).pathname;
await mkdir(output, { recursive: true });
const browser = await launchBrowser();
const context = await browser.newContext({
	viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
const observations = [];
let submissions = 0;
page.on("request", (r) => {
	if (r.method() === "POST" && r.url().endsWith("/api/orders")) submissions++;
});
const capture = async (name) => {
	await page.screenshot({
		animations: "disabled",
		path: output + name + ".png",
		fullPage: true,
	});
	observations.push({ name, text: await page.locator("body").innerText() });
};
try {
	await page.goto(process.env.FRONTEND_TEST_URL ?? "http://127.0.0.1:8001");
	await page
		.getByLabel("访问密码")
		.fill(
			process.env.FRONTEND_TEST_PASSWORD ??
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
	await page.locator(".security-main").filter({ hasText: "贵州茅台" }).click();
	await expect(
		page.getByRole("button", { name: "模拟买入", exact: true }),
	).toBeEnabled();
	await page.getByRole("button", { name: "模拟买入", exact: true }).click();
	await expect(page.locator(".trade-reference")).toContainText("北京时间");
	await expect(page.locator(".trade-reference")).toContainText("CNY 可用现金");
	await page.getByRole("button", { name: "返回标的详情" }).click();
	await expect(page.getByRole("dialog")).toHaveAccessibleName("贵州茅台");
	await page.setViewportSize({ width: 390, height: 844 });
	await page.getByRole("button", { name: "模拟买入", exact: true }).click();
	await page.getByLabel("限价（CNY）").fill("100");
	await page
		.getByLabel("这次交易的理由")
		.fill("受控测试：验证限价预览与资金占用。");
	await page.getByRole("button", { name: "预览费用与资金占用" }).click();
	await page.getByRole("heading", { name: "确认这笔委托" }).waitFor();
	await expect(page.locator(".preview-box")).toContainText("10,005.00");
	await expect(
		page.getByRole("heading", { name: "确认这笔委托" }),
	).toBeInViewport();
	await capture("controlled-preview-phone");
	await page.setViewportSize({ width: 1440, height: 1000 });
	await capture("controlled-preview");
	const axe = (await new AxeBuilder({ page }).analyze()).violations;
	await page.getByLabel("数量（每手 100）").fill("200");
	await expect(page.getByRole("heading", { name: "确认这笔委托" })).toHaveCount(
		0,
	);
	await page.getByLabel("数量（每手 100）").fill("100");
	await page.getByRole("button", { name: "预览费用与资金占用" }).click();
	await page.getByRole("button", { name: "确认提交委托" }).click();
	await expect(page.locator(".order-row")).toHaveCount(1);
	await expect(page.locator(".order-row")).toContainText("等待成交");
	await expect(
		page.getByRole("status").filter({ hasText: "已收到 600519.SS" }),
	).toBeInViewport();
	await capture("controlled-order-accepted");
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await expect(
		page
			.locator(".hero-cash > div")
			.filter({ hasText: "人民币委托占用" })
			.locator("dd"),
	).toContainText("10,005.00");
	await capture("controlled-reserved");
	await page.getByRole("button", { name: "委托", exact: true }).click();
	await page.getByRole("button", { name: "申请撤单" }).click();
	await acceptCancelDialog(page);
	await expect(page.locator(".order-row")).toContainText(/已撤单|撤单确认中/);
	await capture("controlled-cancel");
	await page.getByRole("button", { name: "写复盘" }).click();
	const review = page.getByRole("dialog", { name: "相关计划与复盘" });
	await review.getByRole("button", { name: "事实与笔记" }).click();
	await review
		.getByLabel("计划或委托普通笔记")
		.fill("受控测试委托复盘，保留决策记录。");
	await review.getByRole("button", { name: "保存普通笔记" }).click();
	await expect(review.getByRole("status")).toContainText("普通笔记已保存");
	await review.getByRole("button", { name: "关闭" }).click();
	await capture("controlled-linked-journal");
	await page.getByRole("button", { name: /个人账户/ }).click();
	await page.getByLabel("输入“重新开始”以确认").fill("重新开始");
	await page.getByRole("button", { name: "归档本轮，开始新练习" }).click();
	await expect(page.getByRole("dialog")).toHaveCount(0);
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await expect(page.locator(".asset-value")).toContainText("1,000,000.00");
	await page.getByRole("button", { name: "委托", exact: true }).click();
	await expect(page.locator(".order-row")).toHaveCount(0);
	await capture("controlled-reset");
	await page.getByRole("button", { name: /个人账户/ }).click();
	await page
		.getByRole("button", { name: "查看历史", exact: true })
		.last()
		.click();
	const archive = page.getByRole("dialog", { name: "已归档练习 · 只读" });
	await expect(archive).toContainText("受控测试委托复盘，保留决策记录。");
	await expect(archive).toContainText("600519.SS");
	await expect(archive.getByRole("button", { name: "模拟买入" })).toHaveCount(
		0,
	);
	await capture("controlled-archive-readable");
	await page.setViewportSize({ width: 390, height: 844 });
	await capture("controlled-archive-phone");
	await expect(archive).toContainText("受控测试委托复盘，保留决策记录。");
	const archiveAudit = (await new AxeBuilder({ page }).analyze()).violations;
	if (archiveAudit.length) throw new Error("归档界面无障碍检查失败");
	await page.setViewportSize({ width: 1440, height: 1000 });
	await page.keyboard.press("Escape");
	await page.keyboard.press("Escape");
	await page.getByRole("button", { name: "自选", exact: true }).click();
	await page.locator(".security-main").filter({ hasText: "贵州茅台" }).click();
	await page.getByRole("button", { name: "模拟卖出", exact: true }).click();
	await page.getByLabel("限价（CNY）").fill("100");
	await page.getByRole("button", { name: "预览费用与资金占用" }).click();
	await expect(page.getByRole("alert")).toContainText("可卖持仓不足");
	await capture("controlled-sell-rejected");
	await page.setViewportSize({ width: 390, height: 844 });
	await capture("controlled-trade-mobile");
	await writeFile(
		output + "trading.json",
		JSON.stringify(
			{
				source: "隔离受控行情 + 真实交易引擎 + 临时 SQLite，非生产行情",
				submissions,
				axe,
				observations,
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({
			submissions,
			axe: axe.map((v) => v.id),
			scenarios: observations.map((o) => o.name),
		}),
	);
} catch (error) {
	await capture("controlled-failure");
	throw error;
} finally {
	await browser.close();
}
