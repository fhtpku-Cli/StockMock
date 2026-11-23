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
const evidence = root + ".omo/evidence/phase1/";
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
	const note =
		"提交后，后台取到 20 分钟内的有效报价且仍满足条件时会提醒；刚收盘时仍可能按收盘前的报价提醒，之后要等下次开市。";
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
			JSON.stringify({ source, frontendAsset, scenarios: results }, null, 2),
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
	await page.fill("#reason", "受控验收：第一次买入并同步");
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
	await page.waitForFunction(() =>
		document.querySelector("dialog").innerText.includes("下一轮即会提醒"),
	);
	assert((await text("dialog")).includes("下一轮即会提醒"));
	let shown = await preview();
	assert.match(shown, /当前 0\.00%.*成交后约 1\.00%/);
	assert.match(shown, /1,?009\.50/);
	assert(
		shown.includes("当前报价已达到目标价") &&
			shown.includes(note) &&
			!shown.includes("将立即产生"),
	);
	await save("初次买入并同步进行中计划", shown);
	await activateSubmit();
	await page.waitForFunction(() => !document.querySelector("#quantity"));
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
			shown.includes(note) &&
			!shown.includes("将立即产生"),
	);
	await request("/api/plans", {
		symbol: "AAPL",
		direction: "buy",
		status: "active",
		reason: "受控并发计划",
	});
	await activateSubmit();
	await page.waitForFunction(() =>
		document
			.querySelector("dialog")
			?.innerText.includes("该证券刚有进行中计划"),
	);
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
		shown.includes("其他持仓报价暂缺") && shown.includes("人民币可用资金"),
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
