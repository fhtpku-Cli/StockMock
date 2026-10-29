import { z } from "zod";

export const marketTimezones = {
	CN: "Asia/Shanghai",
	HK: "Asia/Hong_Kong",
	US: "America/New_York",
} as const;
function compareInstant(left: string, right: string): number {
	const whole =
		Date.parse(left.replace(/\.\d+/, "")) -
		Date.parse(right.replace(/\.\d+/, ""));
	if (whole !== 0) return whole;
	const a = /\.(\d+)/.exec(left)?.[1] ?? "";
	const b = /\.(\d+)/.exec(right)?.[1] ?? "";
	const digits = Math.max(a.length, b.length);
	return a.padEnd(digits, "0").localeCompare(b.padEnd(digits, "0"));
}
const instant = z.iso.datetime({ offset: true });
const period = z
	.object({ start: instant, end: instant })
	.refine(
		(value) => compareInstant(value.start, value.end) < 0,
		"支持区间必须按时间递增",
	);
export const marketSessionSchema = z
	.object({
		market: z.enum(["CN", "HK", "US"]),
		as_of: instant,
		market_timezone: z.enum([
			"Asia/Shanghai",
			"Asia/Hong_Kong",
			"America/New_York",
		]),
		market_date: z.iso.date(),
		phase: z.enum([
			"supported",
			"before_open",
			"break",
			"unsupported_closing",
			"after_hours",
			"non_session_day",
			"unknown",
		]),
		current_period: period.nullable(),
		next_period: period.nullable(),
		reason: z.literal("calendar_unavailable").nullable(),
	})
	.superRefine((value, context) => {
		const now = Date.parse(value.as_of);
		if (!Number.isFinite(now)) {
			context.addIssue({ code: "custom", message: "查询时间无效" });
			return;
		}
		const date = new Intl.DateTimeFormat("sv-SE", {
			timeZone: marketTimezones[value.market],
			year: "numeric",
			month: "2-digit",
			day: "2-digit",
		}).format(new Date(now));
		const valid =
			value.market_timezone === marketTimezones[value.market] &&
			value.market_date === date &&
			(value.phase === "supported") === (value.current_period !== null) &&
			(value.phase === "unknown") ===
				(value.reason === "calendar_unavailable") &&
			(value.phase !== "unknown" || value.next_period === null) &&
			(value.phase !== "unsupported_closing" || value.market === "CN") &&
			(!value.current_period ||
				(compareInstant(value.current_period.start, value.as_of) <= 0 &&
					compareInstant(value.as_of, value.current_period.end) < 0)) &&
			(!value.next_period ||
				(compareInstant(value.next_period.start, value.as_of) > 0 &&
					(!value.current_period ||
						compareInstant(value.next_period.start, value.current_period.end) >=
							0)));
		if (!valid)
			context.addIssue({ code: "custom", message: "市场时段字段关系不一致" });
	});
export type MarketSession = z.infer<typeof marketSessionSchema>;
