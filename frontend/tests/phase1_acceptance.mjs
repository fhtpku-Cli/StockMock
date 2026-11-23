// 从候选前端运行。服务与浏览器使用随机的一次性测试口令，证据中只保存业务结果。
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { once } from "node:events";
import { mkdir } from "node:fs/promises";
import { get } from "node:http";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const evidence =
	root + ".omo/evidence/" + (process.env.PHASE1_EVIDENCE ?? "phase1") + "/";
const socket = createServer();
socket.listen(0, "127.0.0.1");
await once(socket, "listening");
const port = socket.address().port;
await new Promise((resolve) => socket.close(resolve));
assert(port !== 8000);
const origin = `http://phase1.localhost:${port}`;
const password = randomUUID();
const child = spawn(
	root + "backend/.venv/bin/python",
	[root + "frontend/tests/phase1_acceptance_server.py", "--port", String(port)],
	{
		cwd: root,
		stdio: ["pipe", "inherit", "inherit"],
		env: {
			PYTHONPATH: root + "backend",
			PAPER_ALLOWED_HOSTS: "127.0.0.1",
			PAPER_ALLOWED_ORIGINS: `http://127.0.0.1:${port}`,
		},
	},
);
child.stdin.end(password + "\n");
const stopped = once(child, "exit");
const spaceName = "StockMock Phase 1 隔离验收";
const finish = `const t=await taskSpace(${JSON.stringify(spaceName)});await t.finish({keep:[]})`;
async function ego(source) {
	const cli = spawn("ego-browser", ["nodejs"], {
		stdio: ["pipe", "inherit", "inherit"],
	});
	cli.stdin.end(source);
	const [code] = await once(cli, "exit");
	assert.equal(code, 0, "Ego Lite 验收失败");
}

async function check({ origin, password, evidence, spaceName }) {
	const assert = (await import("node:assert/strict")).default;
	const { writeFile } = await import("node:fs/promises");
	const task = await taskSpace(spaceName);
	const page = task.page("p1");
	const results = [];
	let frontendAsset;
	const source =
		"受控行情、固定交易时钟、临时 SQLite，实际运行候选页面和交易接口";
	const notes = [
		"提交后，报价仍满足条件时会站内提醒。",
		"只认 20 分钟内的交易时段报价：收盘后约 20 分钟内仍按收盘前报价判断，之后要等下次开市。",
	];
	const conflict = "该证券刚有进行中计划。可带入该计划，或取消同步后重新预览。";
	const existingHint =
		"该证券已有进行中计划，可在「带入已有计划」中选择，沿用其提醒";
	const todayHint =
		"计划复盘日期是今天，提交后通常 1–2 分钟内会出现在站内提醒中";
	const retired = ["冻结人民币", "下一轮即会提醒", "将立即产生", "复查日期"];
	const viewports = [];
	const noRetired = (shown) => retired.every((word) => !shown.includes(word));
	const button = (caption) => "loc=role:button[name='" + caption + "']";
	const wait = (selector) =>
		page.waitForSelector(selector, { state: "visible", timeout: 12000 });
	const click = async (selector) => {
		await wait(selector);
		await page.click(selector);
	};
	const text = (selector) =>
		page.evaluate(
			(sel) => document.querySelector(sel)?.innerText || "",
			selector,
		);
	const request = async (path, body) => {
		assert(path.startsWith("/") && !path.startsWith("//"));
		const result = await page.evaluate(
			async ({ path, body }) => {
				const res = await fetch(
					path,
					body === undefined
						? {}
						: {
								method: "POST",
								headers: { "Content-Type": "application/json" },
								body: JSON.stringify(body),
							},
				);
				return { ok: res.ok, status: res.status, json: await res.json() };
			},
			{ path, body },
		);
		assert(result.ok, "隔离接口失败 " + path + " HTTP " + result.status);
		return result.json;
	};
	const save = async (name, body) => {
		const layout = await page.evaluate(() => {
			const box = document.querySelector(".preview-box");
			const bounds = box?.getBoundingClientRect();
			return {
				width: innerWidth,
				documentWidth: document.documentElement.scrollWidth,
				previewLeft: bounds?.left,
				previewRight: bounds?.right,
				previewClientWidth: box?.clientWidth,
				previewScrollWidth: box?.scrollWidth,
			};
		});
		assert.equal(layout.width, 375);
		assert(layout.documentWidth <= 375);
		if (layout.previewRight != null) {
			assert(layout.previewLeft >= -1 && layout.previewRight <= 376);
			assert(layout.previewScrollWidth <= layout.previewClientWidth + 1);
		}
		results.push({ name, body, layout });
		const shot = await page.cdp("Page.captureScreenshot", { format: "png" });
		await writeFile(
			evidence + String(results.length) + ".png",
			Buffer.from(shot.data, "base64"),
		);
		await writeFile(
			evidence + "browser.json",
			JSON.stringify(
				{ source, frontendAsset, scenarios: results, viewports },
				null,
				2,
			),
		);
		console.log(name + "：通过");
	};
	const open = async (
		symbol,
		side = "buy",
		quantity = "100",
		reload = true,
	) => {
		if (reload) await page.goto(origin);
		await click(button("自选"));
		await click(
			`.discovery > .panel:first-of-type [data-symbol="${symbol}"] .security-main`,
		);
		await click(button(side === "buy" ? "模拟买入" : "模拟卖出"));
		await wait("#limit");
		await page.fill("#limit", "100");
		await page.fill("#quantity", quantity);
	};
	const activateSubmit = async () => {
		await page.focus("dialog button[type=submit]");
		await page.press("dialog button[type=submit]", "Enter");
	};
	const preview = async () => {
		await wait(button("预览费用与资金占用"));
		await activateSubmit();
		await wait(".preview-box");
		return text(".preview-box");
	};
	const reason = "受控验收：第一次买入并同步";
	// 场景 1 预览后逐个视口重新预览：提交按钮无需滚动即可见，且文字与输入框不被操作栏遮挡。
	// 软键盘按 ui.tsx 对 visualViewport 缩小的处理写入可视高度，属于浏览器仿真，不是真机键盘。
	const checkViewport = async ({ name, width, height, keyboard = 0 }) => {
		await page.cdp("Emulation.setDeviceMetricsOverride", {
			width,
			height,
			deviceScaleFactor: 1,
			mobile: true,
		});
		await page.cdp("Emulation.setTouchEmulationEnabled", {
			enabled: true,
			maxTouchPoints: 5,
		});
		await page.waitForFunction(
			(size) => innerWidth === size.width && innerHeight === size.height,
			{ width, height },
		);
		await page.fill("#reason", reason + " ");
		await page.fill("#reason", reason);
		await page.waitForFunction(() => !document.querySelector(".preview-box"));
		if (keyboard)
			await page.evaluate((visible) => {
				document
					.querySelector("dialog")
					.style.setProperty("--visible-height", visible + "px");
			}, height - keyboard);
		const shown = await preview();
		await page.waitForFunction(() =>
			document.querySelector(".trade-actions button[type=submit]"),
		);
		await page.waitForTimeout(400);
		const layout = await page.evaluate((visible) => {
			const dialog = document.querySelector("dialog");
			const heading = dialog.querySelector(".modal-heading");
			const bar = dialog.querySelector(".trade-actions");
			const submit = bar.querySelector("button[type=submit]");
			const title = dialog.querySelector(".preview-box h3");
			const topmost = (element) => {
				const box = element.getBoundingClientRect();
				const hit = document.elementFromPoint(
					box.left + box.width / 2,
					box.top + box.height / 2,
				);
				return hit === element || element.contains(hit);
			};
			const dialogBox = dialog.getBoundingClientRect();
			const submitBox = submit.getBoundingClientRect();
			const result = {
				visibleHeight: visible,
				coarse: matchMedia("(pointer: coarse)").matches,
				compact: matchMedia(
					"(max-width: 700px), (max-height: 500px) and (pointer: coarse)",
				).matches,
				sticky: getComputedStyle(bar).position,
				dialogHeight: Math.round(dialogBox.height),
				submitText: submit.innerText,
				submitVisible:
					submitBox.top >= dialogBox.top - 0.5 &&
					submitBox.bottom <= Math.min(dialogBox.bottom, visible) + 0.5 &&
					topmost(submit),
				previewTitleVisible:
					title.getBoundingClientRect().top >=
						heading.getBoundingClientRect().bottom - 0.5 &&
					title.getBoundingClientRect().bottom <=
						bar.getBoundingClientRect().top + 0.5 &&
					topmost(title),
				horizontalOverflow:
					document.documentElement.scrollWidth > innerWidth ||
					dialog.scrollWidth > dialog.clientWidth + 1,
			};
			const fields = Array.from(
				dialog.querySelectorAll(
					"form input:not([type=hidden]), form select, form textarea",
				),
			).filter(
				(field) =>
					field.getClientRects().length > 0 &&
					!field.closest("details:not([open])"),
			);
			dialog.scrollTop = 0;
			result.hiddenFields = [];
			for (const field of fields) {
				field.focus();
				const box = field.getBoundingClientRect();
				if (
					box.top < heading.getBoundingClientRect().bottom - 0.5 ||
					box.bottom > bar.getBoundingClientRect().top + 0.5 ||
					!topmost(field)
				)
					result.hiddenFields.push(field.id || field.name || field.type);
			}
			document.activeElement?.blur();
			dialog.scrollTop = dialog.scrollHeight;
			const barTop = bar.getBoundingClientRect().top;
			result.coveredAtEnd = Array.from(
				dialog.querySelectorAll(
					"form p, form dt, form dd, form label, form h3, form h4, form input, form select, form textarea",
				),
			)
				.filter(
					(element) =>
						!bar.contains(element) && element.getClientRects().length > 0,
				)
				.filter(
					(element) => element.getBoundingClientRect().bottom > barTop + 0.5,
				)
				.map((element) => element.textContent.trim().slice(0, 24));
			result.fieldCount = fields.length;
			return result;
		}, height - keyboard);
		assert(layout.compact && layout.coarse, name + " 未进入窄屏/触屏布局");
		assert.equal(layout.sticky, "sticky", name);
		assert.equal(layout.submitText, "确认提交委托", name);
		assert(layout.submitVisible, name + " 提交按钮需滚动或被遮挡");
		assert(layout.previewTitleVisible, name + " 预览开头被遮挡");
		assert(!layout.horizontalOverflow, name + " 横向溢出");
		assert.deepEqual(layout.hiddenFields, [], name + " 输入框被遮挡");
		assert.deepEqual(
			layout.coveredAtEnd,
			[],
			name + " 滚到底仍有内容在操作栏下",
		);
		assert(layout.fieldCount >= 6, name);
		viewports.push({ name, width, height, keyboard, layout });
		await page.evaluate(() => {
			document.querySelector("dialog").scrollTop = 0;
			document
				.querySelector(".trade-preview-start")
				.scrollIntoView({ block: "start" });
		});
		const shot = await page.cdp("Page.captureScreenshot", { format: "png" });
		await writeFile(
			evidence + "viewport-" + String(viewports.length) + ".png",
			Buffer.from(shot.data, "base64"),
		);
		console.log(name + " 视口：通过");
		return shown;
	};
	await page.cdp("Emulation.setDeviceMetricsOverride", {
		width: 375,
		height: 812,
		deviceScaleFactor: 1,
		mobile: true,
	});
	await page.goto(origin);
	assert.equal(await page.url(), origin + "/");
	frontendAsset = await page.evaluate(
		() =>
			new URL(document.querySelector('script[type="module"][src]').src)
				.pathname,
	);
	assert.equal((await request("/fixture/status")).name, "phase1-controlled");
	await request("/api/auth/login", { password });
	await open("000001.SZ");
	await page.fill("#reason", reason);
	await click("dialog summary");
	await page.fill("#target", "99");
	await page.fill("#stop", "90");
	await click("dialog input[type=checkbox]");
	await wait("#plan-review-date");
	const today = await page.evaluate(
		() => document.querySelector("#plan-review-date").min,
	);
	await page.evaluate((day) => {
		const field = document.querySelector("#plan-review-date");
		const setter = Object.getOwnPropertyDescriptor(
			HTMLInputElement.prototype,
			"value",
		).set;
		setter.call(field, day);
		field.dispatchEvent(new Event("input", { bubbles: true }));
		field.dispatchEvent(new Event("change", { bubbles: true }));
	}, today);
	await page.waitForFunction(
		(hint) => document.querySelector("dialog").innerText.includes(hint),
		todayHint,
	);
	assert(noRetired(await text("dialog")));
	let shown = await preview();
	assert.match(shown, /仓位占总资产：当前 0\.00%.*成交后约 1\.00%/);
	assert.match(shown, /1,?009\.50，相当于总资产的 0\.10%/);
	assert(
		shown.includes("提交后将同时创建进行中计划，计划复盘日期 " + today) &&
			shown.includes("当前报价已达到目标价") &&
			notes.every((line) => shown.includes(line)) &&
			noRetired(shown),
	);
	for (const viewport of [
		{ name: "375×667", width: 375, height: 667 },
		{ name: "375×812", width: 375, height: 812 },
		{
			name: "软键盘弹出（375×667，可视高度 407）",
			width: 375,
			height: 667,
			keyboard: 260,
		},
		{ name: "横屏触屏（812×375）", width: 812, height: 375 },
	])
		shown = await checkViewport(viewport);
	await page.cdp("Emulation.setDeviceMetricsOverride", {
		width: 375,
		height: 812,
		deviceScaleFactor: 1,
		mobile: true,
	});
	await page.waitForFunction(() => innerWidth === 375 && innerHeight === 812);
	await page.evaluate(() =>
		document
			.querySelector(".trade-preview-start")
			.scrollIntoView({ block: "start" }),
	);
	await save("初次买入并同步进行中计划", shown);
	await activateSubmit();
	await page.waitForFunction(() => !document.querySelector("#quantity"));
	await page.waitForSelector(".trade-receipt", { state: "visible" });
	const receipt = await text(".trade-receipt");
	assert(receipt.includes("已同时创建 000001.SZ 的进行中计划。"), receipt);
	const orders = await request("/api/orders");
	const plans = await request("/api/plans?symbol=000001.SZ&status=active");
	assert.equal(orders.length, 1);
	assert.equal(plans.total, 1);
	assert.equal(orders[0].plan_id, plans.items[0].id);
	assert.equal(plans.items[0].review_date, today);
	await open("000001.SZ", "buy", "100", false);
	await page.fill("#reason", "再次打开同步计划所在证券");
	await page.waitForFunction(
		(id) =>
			Array.from(document.querySelectorAll("#order-plan option")).some(
				(option) => option.value === id,
			),
		plans.items[0].id,
	);
	assert.equal(
		await page.evaluate(
			() => !!document.querySelector("dialog input[type=checkbox]"),
		),
		false,
	);
	assert((await text("dialog")).includes(existingHint));
	await open("600519.SS");
	shown = await preview();
	assert.match(shown, /当前 2\.00%.*成交后约 3\.00%/);
	assert.match(shown, /另有 1 张/);
	await save("已有持仓加仓", shown);
	await open("AAPL", "buy", "10");
	shown = await preview();
	assert.match(shown, /当前 0\.00%.*成交后约 0\.70%/);
	assert(shown.includes("预览汇率") && shown.includes("自动换汇"));
	await save("美股首次买入并补换汇", shown);
	for (const [qty, after, name] of [
		["100", "1.00%", "部分卖出"],
		["200", "0.00%", "清仓"],
	]) {
		await open("600519.SS", "sell", qty);
		shown = await preview();
		assert(shown.includes("当前 2.00%") && shown.includes("成交后约 " + after));
		assert(
			shown.includes(qty + " 股 / 份（撤单确认前不可再卖）") &&
				!shown.includes("占用人民币") &&
				!shown.includes("占用外币"),
		);
		await save(name, shown);
	}
	await open("AAPL", "buy", "10");
	await page.fill("#reason", "受控验收：同步冲突");
	await click("dialog summary");
	await page.fill("#stop", "100");
	await click("dialog input[type=checkbox]");
	shown = await preview();
	assert(
		shown.includes("当前报价已达到止损参考价") &&
			notes.every((line) => shown.includes(line)) &&
			noRetired(shown),
	);
	await request("/api/plans", {
		symbol: "AAPL",
		direction: "buy",
		status: "active",
		reason: "受控并发计划",
	});
	await activateSubmit();
	await page.waitForFunction(
		(message) => document.querySelector("dialog")?.innerText.includes(message),
		conflict,
	);
	// 等计划列表刷新完成，确认最终只留一条提示。
	await page.waitForTimeout(1000);
	const conflictView = await page.evaluate((message) => {
		const dialog = document.querySelector("dialog");
		return {
			count: dialog.innerText.split(message).length - 1,
			alerts: dialog.querySelectorAll("[role=alert]").length,
			cancel: Array.from(dialog.querySelectorAll("button")).some(
				(button) => button.innerText === "取消同步计划",
			),
		};
	}, conflict);
	assert.deepEqual(conflictView, { count: 1, alerts: 0, cancel: true });
	assert.equal((await request("/api/orders")).length, 1);
	assert.equal(
		(await request("/api/plans?symbol=AAPL&status=active")).total,
		1,
	);
	await save("预览后另一端创建计划导致整单冲突", await text("dialog"));
	await request("/fixture/missing-price", {});
	await open("000001.SZ");
	shown = await preview();
	assert(
		shown.includes("其他持仓报价暂缺") &&
			shown.includes("本单占用人民币约为人民币可用资金的") &&
			noRetired(shown),
	);
	assert(!shown.includes("成交后约"));
	await save("其他持仓缺价时展示可用资金比例", shown);
	await activateSubmit();
	await page.waitForFunction(() => !document.querySelector("#quantity"));
	assert.equal((await request("/api/orders")).length, 2);
	await request("/api/auth/logout", {});
	assert.equal(results.length, 7);
}

try {
	let ready = false;
	for (let i = 0; i < 120; i++) {
		if (child.exitCode !== null) throw new Error("隔离服务提前退出");
		try {
			ready = await new Promise((resolve, reject) => {
				get(
					{
						hostname: "127.0.0.1",
						port,
						path: "/fixture/status",
						headers: { Host: "phase1.localhost:" + port },
					},
					(response) => {
						let body = "";
						response.on("data", (chunk) => {
							body += chunk;
						});
						response.on("end", () =>
							resolve(
								response.statusCode === 200 &&
									body.includes("phase1-controlled"),
							),
						);
					},
				).on("error", reject);
			});
			if (ready) break;
		} catch {}
		await new Promise((resolve) => setTimeout(resolve, 250));
	}
	assert(ready, "隔离服务启动超时");
	await mkdir(evidence, { recursive: true });
	await ego(
		`const runAcceptance = ${check.toString()};\nconst fixture = ${JSON.stringify({ origin, password, evidence, spaceName })};\nawait runAcceptance(fixture);`,
	);
	await ego(finish);
} finally {
	child.kill("SIGTERM");
	await stopped;
}
