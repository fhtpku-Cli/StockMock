import { z } from "zod";
import { decimal } from "./schema";
export const snapshotSchema = z.object({
	id: z.string(),
	round_id: z.string(),
	day: z.string(),
	captured_at: z.string(),
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
