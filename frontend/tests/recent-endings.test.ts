import { beforeEach, expect, test } from "bun:test";
import {
	endingChanges,
	markEndingsSeen,
	newOrderIds,
	readSeenEndedAt,
	signedMoney,
} from "../src/recentEndingView";
import { type RecentEnding, recentEndingSchema } from "../src/schema";

// Bun 运行时没有 localStorage；用受控存储替身验证读写失败时的行为。
class FakeStorage {
	private map = new Map<string, string>();
	getItem(key: string): string | null {
		return this.map.has(key) ? (this.map.get(key) as string) : null;
	}
	setItem(key: string, value: string): void {
		this.map.set(key, String(value));
	}
	removeItem(key: string): void {
		this.map.delete(key);
	}
}
class DeniedStorage {
	getItem(): string | null {
		throw new Error("受控禁止读取");
	}
	setItem(): void {
		throw new Error("受控禁止写入");
	}
}
const installStorage = (storage: FakeStorage | DeniedStorage) => {
	Object.defineProperty(globalThis, "localStorage", {
		value: storage,
		configurable: true,
	});
};

const ending = (overrides: Partial<RecentEnding>): RecentEnding =>
	recentEndingSchema.parse({
		order_id: "o-1",
		symbol: "AAPL",
		name: null,
		side: "buy",
		quantity: 1,
		status: "filled",
		status_reason: "",
		currency: "USD",
		fill_price: null,
		filled_at: null,
		ended_at: "2026-11-27T05:00:01+08:00",
		cash_changes: [],
		released_cny: null,
		released_foreign: null,
		released_shares: null,
		...overrides,
	});

beforeEach(() => installStorage(new FakeStorage()));

test("成交单按币种分行显示资金变动，净额为零的币种省略", () => {
	const lines = endingChanges(
		ending({
			cash_changes: [
				{ currency: "CNY", amount: "-7035.00" },
				{ currency: "USD", amount: "-1000.50" },
				{ currency: "HKD", amount: "0.00" },
			],
		}),
	);
	expect(lines).toEqual(["人民币 −¥7,035.00", "美元 −US$1,000.50"]);
});

test("卖出成交的进账带正号", () => {
	const lines = endingChanges(
		ending({
			side: "sell",
			cash_changes: [{ currency: "USD", amount: "998.00" }],
		}),
	);
	expect(lines).toEqual(["美元 +US$998.00"]);
});

test("未成交结束的卖单显示释放占用持仓", () => {
	const lines = endingChanges(
		ending({
			side: "sell",
			status: "cancelled",
			currency: "HKD",
			released_shares: 100,
		}),
	);
	expect(lines).toEqual(["释放占用持仓 100 股 / 份"]);
});

test("未成交结束的买单显示释放占用资金，零值省略", () => {
	const lines = endingChanges(
		ending({
			status: "expired",
			currency: "USD",
			released_cny: "0.00",
			released_foreign: "10.00",
		}),
	);
	expect(lines).toEqual(["释放占用 美元 US$10.00"]);
	expect(
		endingChanges(
			ending({
				status: "terminated",
				currency: "CNY",
				released_cny: "15000.00",
			}),
		),
	).toEqual(["释放占用 人民币 ¥15,000.00"]);
});

test("signedMoney 正负号与零值中性", () => {
	expect(signedMoney("-7035.00", "CNY")).toBe("−¥7,035.00");
	expect(signedMoney("998.00", "USD")).toBe("+US$998.00");
	expect(signedMoney("0.00", "CNY")).toBe("¥0.00");
});

const items = [
	ending({ order_id: "late", ended_at: "2026-11-27T05:00:01+08:00" }),
	ending({ order_id: "early", ended_at: "2026-11-25T06:00:00+08:00" }),
];

test("结束时间晚于已看到位置的条目标新", () => {
	expect(newOrderIds(items, "2026-11-26T00:00:00+08:00")).toEqual(
		new Set(["late"]),
	);
	// 与已看到时间相同不算新出现。
	expect(newOrderIds(items, "2026-11-27T05:00:01+08:00")).toEqual(new Set());
});

test("首次查看时全部标新，读取失败时都不标", () => {
	expect(newOrderIds(items, null)).toEqual(new Set(["late", "early"]));
	expect(newOrderIds(items, undefined)).toEqual(new Set());
});

test("已看到位置只随接口返回的最大结束时间前移", () => {
	markEndingsSeen("r1", items);
	expect(readSeenEndedAt("r1")).toBe("2026-11-27T05:00:01+08:00");
	// 空列表不写入。
	markEndingsSeen("r1", []);
	expect(readSeenEndedAt("r1")).toBe("2026-11-27T05:00:01+08:00");
	// 更早的结束时间不回退。
	markEndingsSeen("r1", [items[1]]);
	expect(readSeenEndedAt("r1")).toBe("2026-11-27T05:00:01+08:00");
	// 轮次隔离。
	expect(readSeenEndedAt("r2")).toBeNull();
});

test("读写失败时不抛错，读失败返回未知", () => {
	installStorage(new DeniedStorage());
	expect(readSeenEndedAt("r1")).toBeUndefined();
	expect(() => markEndingsSeen("r1", items)).not.toThrow();
});
