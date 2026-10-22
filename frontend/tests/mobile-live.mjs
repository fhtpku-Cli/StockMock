import { launchBrowser, baseUrl } from "./browser.mjs";
import { devices, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFile, writeFile, mkdir } from "node:fs/promises";
const out = process.env.FRONTEND_EVIDENCE_DIR
	? process.env.FRONTEND_EVIDENCE_DIR.replace(/\/?$/, "/")
	: new URL("../../.omo/evidence/mobile-ui/", import.meta.url).pathname;
await mkdir(out, { recursive: true });
const address = process.env.FRONTEND_TEST_RESOLVE_ADDRESS;
const hostResolverRules = address
	? `MAP ${new URL(baseUrl).hostname} ${address}`
	: undefined;
const browser = await launchBrowser({ hostResolverRules });
const context = await browser.newContext({ ...devices["iPhone 13"] });
const page = await context.newPage();
const errors = [],
	views = [],
	audits = [];
page.on("pageerror", (e) => errors.push(e.message));
const shot = async (name) => {
	await page.screenshot({ path: out + name + ".png", animations: "disabled" });
	views.push({ name, text: await page.locator("main").innerText() });
};
const readJson = (path) =>
	page.evaluate(async (path) => {
		const response = await fetch(path);
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		return response.json();
	}, path);
try {
	const navigation = await page.goto(baseUrl);
	expect(navigation.status()).toBe(200);
	const secureContext = await page.evaluate(() => window.isSecureContext);
	const tls = await navigation.securityDetails();
	if (baseUrl.startsWith("https:")) {
		expect(secureContext).toBe(true);
		expect(tls?.protocol).toMatch(/^TLS/);
	}
	expect(await page.title()).toContain("持仓练习");
	const font = await page
		.getByLabel("访问密码")
		.evaluate((e) => getComputedStyle(e).fontSize);
	expect(font).toBe("16px");
	await shot("live-login");
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
	await page.getByRole("button", { name: "进入我的空间" }).tap();
	await expect(page.getByRole("heading", { name: "资产概览" })).toBeVisible();
	await shot("live-assets");
	const initialRound = (await readJson("/api/portfolio")).round_id;
	const sessionCookies = (await context.cookies()).map(
		({ secure, httpOnly, sameSite }) => ({ secure, httpOnly, sameSite }),
	);
	if (baseUrl.startsWith("https:")) {
		expect(sessionCookies.length).toBeGreaterThan(0);
		expect(sessionCookies.every((c) => c.secure && c.httpOnly)).toBe(true);
	}
	await context.setOffline(true);
	await expect(page.getByRole("alert")).toContainText("当前离线");
	await shot("live-offline");
	await context.setOffline(false);
	await expect(page.getByText(/当前离线/)).toHaveCount(0);
	const restoredRound = (await readJson("/api/portfolio")).round_id;
	expect(restoredRound).toBe(initialRound);
	for (const label of ["自选", "委托", "复盘"]) {
		await page.getByRole("button", { name: label, exact: true }).tap();
		await shot(`live-${label}`);
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth > innerWidth,
			),
		).toBe(false);
	}
	const targets = await page
		.locator(".nav-item,.icon-button")
		.evaluateAll((elements) =>
			elements
				.filter((el) => el.getClientRects().length)
				.map((el) => {
					const r = el.getBoundingClientRect();
					return {
						label: el.getAttribute("aria-label") || el.textContent,
						width: r.width,
						height: r.height,
					};
				}),
		);
	expect(targets.every((t) => t.width >= 44 && t.height >= 44)).toBe(true);
	audits.push({
		surface: "journal",
		violations: (await new AxeBuilder({ page }).analyze()).violations,
	});
	await page.getByRole("button", { name: "账户设置", exact: true }).tap();
	await expect(page.getByRole("dialog")).toContainText("添加到主屏幕");
	await shot("live-install-guidance");
	audits.push({
		surface: "settings",
		violations: (await new AxeBuilder({ page }).analyze()).violations,
	});
	await page.keyboard.press("Escape");
	await page.reload();
	await expect(page.getByRole("heading", { name: "资产概览" })).toBeVisible();
	await shot("live-reload-session");
	const manifest = await readJson("/manifest.webmanifest");
	const icons = [];
	for (const item of manifest.icons) {
		const bytes = await page.evaluate(async (src) => {
			const r = await fetch(src);
			if (!r.ok) throw new Error(`HTTP ${r.status}`);
			return Array.from(new Uint8Array(await r.arrayBuffer()));
		}, item.src);
		const b = Buffer.from(bytes);
		icons.push({
			src: item.src,
			width: b.readUInt32BE(16),
			height: b.readUInt32BE(20),
		});
	}
	expect(
		await page.evaluate(() =>
			navigator.serviceWorker.getRegistrations().then((r) => r.length),
		),
	).toBe(0);
	expect(errors).toEqual([]);
	for (const audit of audits) expect(audit.violations).toEqual([]);
	await writeFile(
		out + "live.json",
		JSON.stringify(
			{
				source: "真实服务只读；Chromium手机仿真，非iPhone真机",
				resolverOverride: Boolean(address),
				certificateValidation: true,
				secureContext,
				tlsProtocol: tls?.protocol,
				sessionCookies,
				views,
				errors,
				audits,
				targets,
				icons,
				roundPreserved: initialRound === restoredRound,
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({
			views: views.length,
			resolverOverride: Boolean(address),
			secureContext,
			tlsProtocol: tls?.protocol,
			sessionCookies,
			errors,
			axe: audits.map((a) => [a.surface, a.violations.length]),
			icons,
			touchTargets: targets.length,
			roundPreserved: true,
		}),
	);
} catch (error) {
	await page.screenshot({
		path: out + "live-failure.png",
		animations: "disabled",
	});
	throw error;
} finally {
	await browser.close();
}
