import { expect, test } from "bun:test";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { refreshAfterRoundReset } from "../src/refreshAfterRoundReset";

test.each([false, true])(
	"重开时旧查询未完且已有缓存=%p，新轮先返回，旧结果晚到不能回写",
	async (hasCachedData) => {
		const client = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
		const oldBody = Promise.withResolvers<{
			round_id: string;
			count: number;
		}>();
		const oldValue = { round_id: "old-round", count: 3 };
		const newValue = { round_id: "new-round", count: 0 };
		const calls = { portfolio: 0, orders: 0 };
		const signals: AbortSignal[] = [];
		const unsubscribes = ["portfolio", "orders"].map((key) => {
			if (hasCachedData) client.setQueryData([key], oldValue);
			const observer = new QueryObserver(client, {
				queryKey: [key],
				queryFn: ({ signal }) => {
					const count =
						key === "portfolio" ? ++calls.portfolio : ++calls.orders;
					if (count === 1) {
						signals.push(signal);
						return oldBody.promise;
					}
					return Promise.resolve(newValue);
				},
			});
			return observer.subscribe(() => {});
		});
		try {
			const refresh = refreshAfterRoundReset(client);
			expect(calls).toEqual({ portfolio: 2, orders: 2 });
			expect(signals.every((signal) => signal.aborted)).toBe(true);
			await refresh;
			expect(client.getQueryData(["portfolio"])).toEqual(newValue);
			expect(client.getQueryData(["orders"])).toEqual(newValue);
			oldBody.resolve(oldValue);
			await oldBody.promise;
			await Promise.resolve();
			expect(client.getQueryData(["portfolio"])).toEqual(newValue);
			expect(client.getQueryData(["orders"])).toEqual(newValue);
		} finally {
			oldBody.resolve(oldValue);
			for (const unsubscribe of unsubscribes) unsubscribe();
			client.clear();
		}
	},
);

test("重开清除未挂载委托页的旧轮缓存，保留登录状态及其它刷新", async () => {
	const client = new QueryClient();
	const session = { authenticated: true, configured: true };
	client.setQueryData(["session"], session);
	client.setQueryData(["orders"], [{ id: "old-order" }]);
	client.setQueryData(["rounds"], [{ id: "old-round" }]);
	try {
		await refreshAfterRoundReset(client);
		expect(client.getQueryData(["orders"])).toBeUndefined();
		expect(client.getQueryData(["session"])).toEqual(session);
		expect(client.getQueryState(["rounds"])?.isInvalidated).toBe(true);
	} finally {
		client.clear();
	}
});
