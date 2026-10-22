import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, webkit } from "@playwright/test";
import { launchBrowser } from "./browser.mjs";
import { stopServer } from "./tradability-helpers.mjs";

const root = resolve(import.meta.dirname, "../..");
const output = resolve(root, ".omo/evidence/iteration-0.2.0/frontend");
const url = "http://127.0.0.1:8202";
const observations = [];
await mkdir(output, { recursive: true });
for (const engine of ["chromium", "webkit"]) {
	let browser, server, page;
	const log = createWriteStream(resolve(output, `${engine}-server.log`));
	try {
		server = spawn(
			resolve(root, "backend/.venv/bin/python"),
			["backend/tests/iteration_server.py"],
			{
				cwd: root,
				env: {
					...process.env,
					PYTHONPATH: "backend",
					CANDIDATE_PORT: "8202",
					CANDIDATE_DB: resolve(output, `${engine}-${randomUUID()}.sqlite3`),
				},
				stdio: ["ignore", "pipe", "pipe"],
			},
		);
		server.stdout.pipe(log);
		server.stderr.pipe(log);
		await expect
			.poll(
				async () => {
					try {
						return (await fetch(`${url}/api/auth/session`)).status;
					} catch {
						return 0;
					}
				},
				{ timeout: 20000 },
			)
			.toBe(200);
		browser =
			engine === "chromium"
				? await launchBrowser()
				: await webkit.launch({ headless: true });
		const context = await browser.newContext({
			viewport: { width: 1440, height: 1000 },
			reducedMotion: "reduce",
		});
		page = await context.newPage();
		page.setDefaultTimeout(15000);
		const errors = [];
		page.on("pageerror", (error) => errors.push(error.message));
		const capture = async (name) => {
			await page.screenshot({ path: resolve(output, `${engine}-${name}.png`) });
			const overflow = await page.evaluate(() =>
				[...document.querySelectorAll(".modal[open], .main-scroll")]
					.filter((element) => element.getClientRects().length > 0)
					.map((element) => ({
						width: element.clientWidth,
						scroll: element.scrollWidth,
					}))
					.filter(({ width, scroll }) => scroll > width + 1),
			);
			expect(overflow, `${name} must not overflow`).toEqual([]);
			const smallSelects = await page
				.locator("select:visible")
				.evaluateAll((items) =>
					items
						.filter((item) => item.getBoundingClientRect().height < 44)
						.map((item) => ({
							id: item.id,
							height: item.getBoundingClientRect().height,
						})),
				);
			expect(smallSelects, `${name} select touch targets`).toEqual([]);
			observations.push({
				engine,
				name,
				passed: true,
				artifact: `${engine}-${name}.png`,
				text: await page.locator("body").innerText(),
			});
		};
		const login = async (target) => {
			await target.goto(url);
			await target.getByLabel("访问密码").fill("iteration-0.2.0-local-only");
			await target.getByRole("button", { name: "进入我的空间" }).click();
			await expect(
				target.getByRole("heading", { name: "资产概览" }),
			).toBeVisible();
		};
		const api = async (path, data, method = "POST") => {
			const response =
				data === undefined
					? await context.request.get(`${url}/api/${path}`)
					: await context.request.fetch(`${url}/api/${path}`, {
							method,
							data,
							headers: { Origin: url },
						});
			expect(response.ok(), `${path}: ${await response.text()}`).toBe(true);
			return response.json();
		};
		const control = async (path) => {
			const response = await context.request.post(`${url}/_test/${path}`);
			expect(response.ok()).toBe(true);
			return response.json();
		};
		const open = async (target, symbol = "510300.SS") => {
			await target.getByRole("button", { name: "自选", exact: true }).click();
			await target.getByLabel("筛选已可交易证券").fill(symbol);
			await target
				.locator(`[data-symbol="${symbol}"] .security-main`)
				.first()
				.click();
			await expect(
				target
					.getByRole("dialog")
					.getByRole("heading", { name: "我的交易计划", exact: true }),
			).toBeVisible();
		};
		await login(page);
		await capture("assets-desktop");
		await page.getByRole("button", { name: "自选", exact: true }).click();
		await expect(
			page.getByRole("heading", { name: "已可交易", exact: true }),
		).toBeVisible();
		const catalog = page.locator("section.panel").filter({
			has: page.getByRole("heading", { name: "已可交易", exact: true }),
		});
		await expect(catalog.locator(".security-row")).toHaveCount(24);
		await catalog.getByRole("button", { name: /再显示/ }).click();
		expect(await catalog.locator(".security-row").count()).toBeGreaterThan(24);
		await capture("discover-desktop");
		for (const width of [390, 320]) {
			await page.setViewportSize({ width, height: 844 });
			await page
				.getByRole("heading", { name: "已可交易", exact: true })
				.scrollIntoViewIfNeeded();
			await capture(`discover-${width}`);
		}
		await page.setViewportSize({ width: 1440, height: 1000 });
		await page.route("**/api/instruments/search?*", (route) =>
			route.abort("failed"),
		);
		await page.getByLabel("搜索代码或名称").fill("NO-CONNECTION");
		await expect(page.getByRole("alert")).toContainText("在线结果当前不可用");
		await expect(catalog.locator(".security-row").first()).toBeVisible();
		await page.getByRole("alert").scrollIntoViewIfNeeded();
		await capture("online-error-retains-local");
		await page.unroute("**/api/instruments/search?*");
		const instruments = await api("instruments");
		await page.route("**/api/instruments/search?*", (route) =>
			route.fulfill({
				json: {
					items: instruments.filter((item) => item.symbol === "510300.SS"),
					source: "受控在线搜索",
					message: "仅验证前端恢复，不代表真实来源",
				},
			}),
		);
		await page.getByRole("alert").getByRole("button", { name: "重试" }).click();
		await expect(
			page.getByText("仅验证前端恢复，不代表真实来源", { exact: false }),
		).toBeVisible();
		await capture("online-recovered");
		await page.getByLabel("搜索代码或名称").fill("");
		await open(page);
		await page.getByRole("button", { name: "新建计划", exact: true }).click();
		await page.getByLabel("计划方向", { exact: true }).selectOption("buy");
		await page
			.getByLabel("计划理由", { exact: false })
			.fill(`受控计划-初始判断 ${"https://example.test/" + "a".repeat(160)}`);
		await page
			.getByLabel("目标参考价（CNY）", { exact: true })
			.fill("101.1234");
		await page.getByLabel("止损参考价（CNY）", { exact: true }).fill("99.0001");
		await page
			.getByLabel("计划复盘日期（北京时间）", { exact: false })
			.fill("2026-10-23");
		await page
			.getByLabel("失效条件", { exact: false })
			.fill("基本假设改变后重新评估，不自动买卖。");
		for (const width of [390, 320]) {
			await page.setViewportSize({ width, height: 844 });
			await page.locator("#plan-date").scrollIntoViewIfNeeded();
			await capture(`plan-form-${width}`);
		}
		await context.setOffline(true);
		await expect(
			page.getByRole("button", { name: "保存计划", exact: true }),
		).toBeDisabled();
		await capture("plan-offline-320");
		await context.setOffline(false);
		await expect(
			page.getByRole("button", { name: "保存计划", exact: true }),
		).toBeEnabled();
		for (let step = 0; step < 12; step++) {
			await page.keyboard.press("Tab");
			expect(
				await page.evaluate(() => !!document.activeElement?.closest("dialog")),
			).toBe(true);
		}
		await page.getByRole("button", { name: "保存计划", exact: true }).click();
		await expect(
			page.getByRole("status").filter({ hasText: "计划已保存" }),
		).toBeVisible();
		let plan = (await api("plans?symbol=510300.SS")).items[0];
		expect(plan.status).toBe("draft");
		expect(plan.target_price).toBe("101.1234");
		await page.getByRole("button", { name: "编辑计划", exact: true }).click();
		await page.getByLabel("计划状态", { exact: true }).selectOption("active");
		await page.getByRole("button", { name: "保存计划", exact: true }).click();
		await expect(
			page.getByRole("status").filter({ hasText: "计划已保存" }),
		).toBeVisible();
		await page.getByRole("button", { name: "编辑计划", exact: true }).click();
		const secondContext = await browser.newContext({
			viewport: { width: 390, height: 844 },
		});
		const second = await secondContext.newPage();
		await login(second);
		await open(second);
		await expect(second.locator(".plan-record")).toContainText(
			"受控计划-初始判断",
		);
		await second.getByRole("button", { name: "编辑计划", exact: true }).click();
		await second
			.getByLabel("计划理由", { exact: false })
			.fill("第二设备修订：等待估值与盈利确认");
		await second.getByRole("button", { name: "保存计划", exact: true }).click();
		await expect(
			second.getByRole("status").filter({ hasText: "计划已保存" }),
		).toBeVisible();
		await page
			.getByLabel("计划理由", { exact: false })
			.fill("第一设备过期编辑，不应覆盖");
		await page.getByRole("button", { name: "保存计划", exact: true }).click();
		await expect(
			page.getByRole("button", { name: "重新载入最新计划" }),
		).toBeVisible();
		await capture("conflict-320");
		await page.getByRole("button", { name: "重新载入最新计划" }).click();
		await expect(page.getByLabel("计划理由", { exact: false })).toHaveValue(
			"第二设备修订：等待估值与盈利确认",
		);
		await page.getByRole("button", { name: "取消编辑" }).click();
		await secondContext.close();
		await page.getByRole("button", { name: "模拟买入", exact: true }).click();
		plan = (await api("plans?symbol=510300.SS")).items[0];
		await page
			.getByLabel("带入已有计划", { exact: false })
			.selectOption(plan.id);
		await expect(
			page.getByLabel("这次交易的理由", { exact: false }),
		).toHaveValue(plan.reason);
		await page.getByLabel(/^数量/).fill("200");
		await page.getByLabel("限价（CNY）", { exact: true }).fill("100");
		await page.getByRole("button", { name: "预览费用与资金占用" }).click();
		await expect(
			page.getByRole("button", { name: "确认提交委托" }),
		).toBeVisible();
		await capture("plan-order-preview-320");
		let lostReply = false;
		await page.route("**/api/orders", async (route) => {
			if (route.request().method() === "POST" && !lostReply) {
				lostReply = true;
				await route.fetch();
				await route.abort("failed");
			} else await route.continue();
		});
		await page.getByRole("button", { name: "确认提交委托" }).click();
		await expect(
			page.getByRole("button", { name: "重试确认委托结果" }),
		).toBeVisible();
		await page.getByRole("button", { name: "重试确认委托结果" }).click();
		await expect(page.getByRole("heading", { name: "委托记录" })).toBeVisible();
		await page.unroute("**/api/orders");
		const placed = (await api("orders"))[0];
		expect(placed.plan_id).toBe(plan.id);
		expect((await api("orders")).length).toBe(1);
		await api(
			`plans/${plan.id}`,
			{
				symbol: plan.symbol,
				direction: plan.direction,
				status: plan.status,
				reason: "下单后修改的计划",
				target_price: plan.target_price,
				stop_price: plan.stop_price,
				review_date: plan.review_date,
				invalidation: plan.invalidation,
				version: plan.version,
			},
			"PUT",
		);
		expect((await api("orders"))[0].plan_snapshot.reason).toBe(
			"第二设备修订：等待估值与盈利确认",
		);
		await control("advance");
		expect((await api("orders"))[0].status).toBe("filled");
		await control("control?price=110&status=valid");
		await page.getByRole("button", { name: "资产", exact: true }).click();
		await expect(page.locator(".alert-list article").first()).toBeVisible();
		await capture("alerts-320");
		await page
			.locator(".alert-list")
			.getByRole("button", { name: "确认已读" })
			.first()
			.click();
		await page.getByRole("button", { name: "全部提示", exact: true }).click();
		await expect(page.locator(".alert-list")).toContainText("已读");
		await control("control?status=failure");
		await open(page);
		await expect(page.locator(".plans-section")).toContainText(
			"暂不能判断是否到价",
		);
		await capture("monitoring-unavailable-320");
		await page
			.getByRole("dialog")
			.getByRole("button", { name: "关闭", exact: true })
			.click();
		await control("control?price=110&status=valid");
		await control("advance?minutes=4320");
		await login(page);
		await open(page);
		await page.getByRole("button", { name: "模拟卖出", exact: true }).click();
		await page
			.getByLabel("带入已有计划", { exact: false })
			.selectOption(plan.id);
		await page.getByLabel(/^数量/).fill("100");
		await page.getByLabel("限价（CNY）", { exact: true }).fill("110");
		await page.getByRole("button", { name: "预览费用与资金占用" }).click();
		await page.getByRole("button", { name: "确认提交委托" }).click();
		await expect(page.getByRole("heading", { name: "委托记录" })).toBeVisible();
		await control("advance");
		await page.getByRole("button", { name: "复盘", exact: true }).click();
		const card = page.locator(".review-card").filter({ hasText: "510300.SS" });
		await card.getByRole("button", { name: "查看计划与实际结果" }).click();
		await expect(card).toContainText("部分卖出");
		await expect(card).toContainText("第二设备修订：等待估值与盈利确认");
		await expect(card).toContainText("下单后修改的计划");
		await card.locator(".review-detail").scrollIntoViewIfNeeded();
		await capture("review-partial-320");
		await card
			.getByLabel("计划或委托复盘内容")
			.fill("受控复盘：分批卖出后仍持有100份，净收益只统计卖出部分。");
		await card.getByRole("button", { name: "保存复盘", exact: true }).click();
		await page.getByLabel("复盘状态", { exact: true }).selectOption("reviewed");
		await card.getByRole("button", { name: "查看计划与实际结果" }).click();
		await expect(card).toContainText("受控复盘：分批卖出");
		await card.getByRole("button", { name: "结束本计划" }).click();
		await card.getByRole("button", { name: "确认结束计划" }).click();
		await expect(card).toContainText("已结束");
		await capture("reviewed-ended-320");
		await page.setViewportSize({ width: 1440, height: 1000 });
		await capture("reviewed-desktop");
		const axe = await new AxeBuilder({ page }).analyze();
		expect(
			axe.violations.filter(
				(v) => v.impact === "critical" || v.impact === "serious",
			),
		).toEqual([]);
		for (const exportPath of [
			"plans/export.csv",
			"reviews/export.csv",
			"alerts/export.csv",
		]) {
			const response = await context.request.get(`${url}/api/${exportPath}`);
			expect(response.ok()).toBe(true);
			expect(await response.text()).toContain("510300.SS");
		}
		const cancelled = await api("orders", {
			request_id: randomUUID(),
			symbol: "600519.SS",
			side: "buy",
			order_type: "limit",
			quantity: 100,
			limit_price: "90",
			reason: "受控旧单撤单判断",
		});
		await api(`orders/${cancelled.id}/cancel`, {});
		await control("advance");
		await page.getByRole("button", { name: "刷新数据", exact: true }).click();
		await page.getByLabel("复盘状态", { exact: true }).selectOption("all");
		const legacy = page
			.locator(".review-card")
			.filter({ hasText: "600519.SS" })
			.filter({ hasText: "未关联计划的委托" });
		await legacy.getByRole("button", { name: "查看计划与实际结果" }).click();
		await expect(legacy).toContainText("已撤单");
		await expect(legacy).toContainText("尚未进入待复盘");
		await legacy
			.getByLabel("计划或委托复盘内容")
			.fill("受控撤单复盘：未成交，不计盈利交易。");
		await legacy.getByRole("button", { name: "保存复盘", exact: true }).click();
		await expect(legacy).toContainText("已复盘");
		await capture("cancelled-legacy-order");
		const archivedNoteCount = (await api("journal")).length + 510;
		await control("history?count=510");
		await page.locator(".account-button").click();
		await page.getByLabel("输入“重新开始”以确认").fill("重新开始");
		await page.getByRole("button", { name: "归档本轮，开始新练习" }).click();
		await expect(page.getByRole("dialog")).toHaveCount(0);
		await page.locator(".account-button").click();
		await page
			.getByRole("button", { name: "查看历史", exact: true })
			.first()
			.click();
		const archive = page.getByRole("dialog", {
			name: "已归档练习 · 只读",
			exact: true,
		});
		await expect(
			archive.getByRole("heading", { name: "历史计划与交易复盘" }),
		).toBeVisible();
		await expect(
			archive.getByRole("button", { name: "保存复盘", exact: true }),
		).toHaveCount(0);
		await expect(
			archive.getByRole("button", { name: "确认已读", exact: true }),
		).toHaveCount(0);
		const noteSection = archive
			.locator("section")
			.filter({ has: page.getByRole("heading", { name: /历史复盘笔记/ }) });
		await expect(noteSection.locator(".journal-list > article")).toHaveCount(
			25,
		);
		while (
			(await noteSection.locator(".journal-list > article").count()) <
			archivedNoteCount
		) {
			const before = await noteSection
				.locator(".journal-list > article")
				.count();
			await noteSection
				.getByRole("button", { name: "加载更多", exact: true })
				.click();
			await expect
				.poll(() => noteSection.locator(".journal-list > article").count())
				.toBeGreaterThan(before);
		}
		await expect(noteSection.locator(".journal-list > article")).toHaveCount(
			archivedNoteCount,
		);
		for (const width of [1440, 390, 320]) {
			await page.setViewportSize({
				width,
				height: width === 1440 ? 1000 : 844,
			});
			await archive
				.getByRole("heading", { name: "历史计划与交易复盘" })
				.scrollIntoViewIfNeeded();
			await capture(`archive-${width}`);
			await archive.evaluate((element) => {
				element.scrollTop = element.scrollHeight;
			});
			await capture(`archive-end-${width}`);
		}
		expect(errors).toEqual([]);
		observations.push({
			engine,
			name: "complete",
			passed: true,
			consoleErrors: errors,
			axeSerious: 0,
			archivedNotes: archivedNoteCount,
		});
	} catch (error) {
		if (page) {
			await page.screenshot({ path: resolve(output, `${engine}-failure.png`) });
			await writeFile(
				resolve(output, `${engine}-failure.txt`),
				await page.locator("body").innerText(),
			);
		}
		observations.push({
			engine,
			name: "failure",
			passed: false,
			error: String(error),
		});
		throw error;
	} finally {
		await writeFile(
			resolve(output, "observations.json"),
			JSON.stringify(observations, null, 2),
		);
		if (browser) await browser.close();
		await stopServer(server);
		log.end();
	}
}
console.log(
	JSON.stringify({ passed: true, observations: observations.length, output }),
);
