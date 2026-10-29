import { describe, expect, test } from "bun:test";
import { fxDate, fxRate } from "../src/fxDisplay";
import { snapshotSchema, snapshotSegments } from "../src/history";
import { candidateIntervalSchema } from "../src/schema";

describe("0.4.5展示边界", () => {
	test("汇率十进制六位舍入，不损失大数精度", () => {
		expect(fxRate("7.123456789123456789")).toBe("7.123457");
		expect(fxRate("0.9999995")).toBe("1.000000");
		expect(fxRate("9007199254740993.00000149")).toBe("9007199254740993.000001");
		expect(fxRate("7")).toBe("7.000000");
		expect(fxRate(null)).toBe("未知");
	});
	test("日频日期不转换为北京时间发布时间", () => {
		expect(fxDate("2026-10-28T00:00:00Z")).toBe("2026-10-28 日频参考");
		expect(fxDate("2026-10-28T23:00:00-04:00")).toBe("2026-10-28 日频参考");
		expect(fxDate(null)).toBe("日频参考日期未知");
	});
	test("候选状态区分确实无候选与未知", () => {
		for (const status of ["none", "unknown"])
			expect(
				candidateIntervalSchema.parse({
					status,
					start: null,
					end: null,
					reason: "受控说明",
				}).status,
			).toBe(status);
	});
	test("首次失败元数据兼容旧快照并在当天恢复后连接实际完整记录", () => {
		const old = {
			id: "a",
			round_id: "r",
			day: "2026-10-28",
			captured_at: "2026-10-28T03:00:00Z",
			total_assets_cny: "100",
			pnl_cny: "0",
			status: "complete",
			issues: [],
			price_times: {},
		};
		const first = snapshotSchema.parse(old);
		expect(first.first_failure).toBeUndefined();
		const failed = snapshotSchema.parse({
			...old,
			id: "b",
			day: "2026-10-29",
			status: "missing",
			total_assets_cny: null,
		});
		expect(snapshotSegments([first, failed])[0]?.length).toBe(1);
		const restored = snapshotSchema.parse({
			...failed,
			status: "complete",
			captured_at: "2026-10-29T06:00:00Z",
			total_assets_cny: "110",
			first_failure: {
				captured_at: "2026-10-29T01:00:00Z",
				status: "missing",
				issues: ["来源不可用"],
			},
		});
		expect(snapshotSegments([first, restored])[0]?.length).toBe(2);
		expect(restored.first_failure?.issues).toEqual(["来源不可用"]);
	});
});
