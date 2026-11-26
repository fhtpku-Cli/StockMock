import { z } from "zod";
import { money, time } from "./api";
import { decimal } from "./schema";
export const snapshotSchema = z.object({
	id: z.string(),
	round_id: z.string(),
	day: z.string(),
	captured_at: z.string(),
	first_failure: z
		.object({
			captured_at: z.string(),
			status: z.enum(["missing", "stale"]),
			issues: z.array(z.string()),
		})
		.nullable()
		.optional(),
	total_assets_cny: decimal.nullable(),
	pnl_cny: decimal.nullable(),
	status: z.enum(["complete", "stale", "missing"]),
	issues: z.array(z.string()),
	price_times: z.record(z.string(), z.string()),
});
export type Snapshot = z.infer<typeof snapshotSchema>;
export function snapshotSegments(
	rows: readonly Snapshot[],
): readonly (readonly Snapshot[])[] {
	const segments: Snapshot[][] = [];
	let current: Snapshot[] = [];
	for (const row of [...rows].sort((a, b) => a.day.localeCompare(b.day))) {
		const previous = current.at(-1);
		if (row.status !== "complete" || row.total_assets_cny === null) {
			current = [];
			continue;
		}
		if (
			!previous ||
			Date.parse(row.day) - Date.parse(previous.day) !== 86400000
		) {
			current = [];
			segments.push(current);
		}
		current.push(row);
	}
	return segments;
}

const drawdownPointSchema = z.object({
	day: z.string(),
	captured_at: z.string(),
	total_assets_cny: decimal,
});
// 12 资产高点回落；旧后端不返回时页面不显示回落行。
export const drawdownSchema = z.object({
	status: z.enum(["computed", "no_drop", "insufficient"]),
	reason: z.string(),
	peak: drawdownPointSchema.nullable(),
	trough: drawdownPointSchema.nullable(),
	amount_cny: decimal.nullable(),
	percent: decimal.nullable(),
	segments_used: z.number().int(),
	broken_days: z.number().int(),
});
export type Drawdown = z.infer<typeof drawdownSchema>;
type DrawdownPoint = z.infer<typeof drawdownPointSchema>;

/** 12 资产高点回落：broken_days 大于 0 时的附注。 */
export const DRAWDOWN_BROKEN_NOTE =
	"只在连续完整记录内计算，跨越未记录或不完整日期的变化不计入。";

/** 回落端点日期：同年显示 MM-DD，高点低点跨年时补全年份。 */
export function drawdownDayLabel(day: string, otherDay: string | null): string {
	return otherDay !== null && day.slice(0, 4) !== otherDay.slice(0, 4)
		? day
		: day.slice(5);
}

/** 回落端点在摘要里的写法：日期加北京时间记录时间。 */
export function drawdownPointLabel(
	point: Pick<DrawdownPoint, "day" | "captured_at">,
	otherDay: string | null,
): string {
	return `${drawdownDayLabel(point.day, otherDay)} ${time(point.captured_at).slice(-5)}`;
}

/** 资产轨迹摘要下的回落一行，三种状态文案见详案 3.3。 */
export function drawdownSummary(d: Drawdown): string {
	if (d.status === "no_drop") return "区间内连续完整记录中没有出现回落";
	if (d.status === "insufficient") return "连续完整记录不足两天，无法计算回落";
	if (!d.peak || !d.trough || d.amount_cny === null || d.percent === null)
		return d.reason;
	return `区间内最大回落 −${Number(d.percent).toFixed(2)}%（${money(d.amount_cny)}）：从 ${drawdownPointLabel(d.peak, d.trough.day)} 的 ${money(d.peak.total_assets_cny)} 回落到 ${drawdownPointLabel(d.trough, d.peak.day)} 的 ${money(d.trough.total_assets_cny)}`;
}

export const snapshotRangeSchema = z.object({
	round_id: z.string(),
	start_date: z.string().nullable(),
	end_date: z.string().nullable(),
	items: z.array(snapshotSchema),
	recorded_days: z.number().int(),
	complete_days: z.number().int(),
	missing_days: z.number().int(),
	first: snapshotSchema.nullable(),
	last: snapshotSchema.nullable(),
	change_cny: decimal.nullable(),
	continuous: z.boolean(),
	reason: z.string(),
	drawdown: drawdownSchema.optional(),
});
