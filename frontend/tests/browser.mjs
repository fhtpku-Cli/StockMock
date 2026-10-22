import { chromium } from "@playwright/test";
import { existsSync } from "node:fs";
export const baseUrl = process.env.FRONTEND_TEST_URL ?? "http://127.0.0.1:8000";
export function launchBrowser({ hostResolverRules } = {}) {
	const systemChrome =
		"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
	const executablePath =
		process.env.CHROME_PATH ??
		(existsSync(systemChrome) ? systemChrome : undefined);
	return chromium.launch({
		...(executablePath ? { executablePath } : {}),
		headless: true,
		...(hostResolverRules
			? { args: [`--host-resolver-rules=${hostResolverRules}`] }
			: {}),
	});
}
