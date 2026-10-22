import ky, { HTTPError } from "ky";
import { z } from "zod";
export class ApiError extends Error {
	constructor(
		readonly code: string,
		message: string,
		readonly status?: number,
	) {
		super(message);
		this.name = "ApiError";
	}
}
export const SESSION_EXPIRED = "stockmock:session-expired";
const http = ky.create({
	prefixUrl: "/api",
	credentials: "same-origin",
	timeout: 25000,
	retry: { limit: 1, methods: ["get"] },
	hooks: {
		afterResponse: [
			(request, _options, response) => {
				if (
					response.status === 401 &&
					!new URL(request.url).pathname.endsWith("/auth/login")
				)
					window.dispatchEvent(new Event(SESSION_EXPIRED));
				return response;
			},
		],
	},
});
const errorSchema = z.object({
	detail: z.union([
		z.string(),
		z.object({ code: z.string(), message: z.string() }),
	]),
});
export async function api<T>(
	path: string,
	schema: z.ZodType<T>,
	body?: Readonly<Record<string, unknown>>,
	options?: { readonly timeout?: number; readonly method?: "post" | "put" },
): Promise<T> {
	try {
		if (body && !navigator.onLine)
			throw new ApiError("offline", "当前离线，操作未提交。请联网后重新确认。");
		const response = body
			? await http(path, {
					json: body,
					method: options?.method ?? "post",
					...options,
				})
			: await http.get(path, options);
		const raw: unknown = await response.json();
		const result = schema.safeParse(raw);
		if (!result.success)
			throw new ApiError(
				"invalid_response",
				"返回的数据格式不完整，请稍后重试。",
			);
		return result.data;
	} catch (error) {
		if (error instanceof HTTPError) {
			const raw: unknown = await error.response.json().catch(() => null);
			const parsed = errorSchema.safeParse(raw);
			if (parsed.success) {
				const detail = parsed.data.detail;
				throw new ApiError(
					typeof detail === "string" ? "request_failed" : detail.code,
					typeof detail === "string" ? detail : detail.message,
					error.response.status,
				);
			}
			throw new ApiError(
				"request_failed",
				"请求未完成，请稍后重试。",
				error.response.status,
			);
		}
		if (error instanceof ApiError) throw error;
		if (error instanceof Error)
			throw new ApiError("connection", "暂时无法连接服务，请检查连接后重试。");
		throw error;
	}
}
export async function removeWatch(symbol: string): Promise<void> {
	try {
		await http.delete(`watchlist/${encodeURIComponent(symbol)}`);
	} catch (error) {
		if (error instanceof Error)
			throw new ApiError("request_failed", "移除失败，请重试。");
		throw error;
	}
}
export const errorMessage = (error: unknown): string =>
	error instanceof Error ? error.message : "操作未完成，请重试。";
const currencies = new Map<string, Intl.NumberFormat>();
export const money = (value: string | null, currency = "CNY"): string => {
	if (value === null) return "—";
	let format = currencies.get(currency);
	if (!format) {
		format = new Intl.NumberFormat("zh-CN", {
			style: "currency",
			currency,
			minimumFractionDigits: 2,
			maximumFractionDigits: 2,
		});
		currencies.set(currency, format);
	}
	return format.format(Number(value));
};
const numberFormat = new Intl.NumberFormat("zh-CN");
export const number = (value: number): string => numberFormat.format(value);
const dateFormat = new Intl.DateTimeFormat("zh-CN", {
	timeZone: "Asia/Shanghai",
	month: "2-digit",
	day: "2-digit",
	hour: "2-digit",
	minute: "2-digit",
	hour12: false,
});
export const time = (value: string | null): string =>
	value ? dateFormat.format(new Date(value)) : "尚无记录";

export const price = (
	value: string | null,
	currency: string,
	tickSize?: string,
): string => {
	if (value === null) return "—";
	const digits = (tickSize ?? value).split(".")[1]?.length ?? 0;
	const precision = Math.min(Math.max(digits, 2), 8);
	const key = `${currency}:${precision}`;
	let formatter = currencies.get(key);
	if (!formatter) {
		formatter = new Intl.NumberFormat("zh-CN", {
			style: "currency",
			currency,
			minimumFractionDigits: precision,
			maximumFractionDigits: precision,
		});
		currencies.set(key, formatter);
	}
	return formatter.format(Number(value));
};
