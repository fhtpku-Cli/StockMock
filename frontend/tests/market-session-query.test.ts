import { expect, test } from "bun:test";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import {
	marketSessionQuery,
	refreshMarketSession,
	refreshWithMarketSession,
} from "../src/marketSessionQuery";

for (const globalRefresh of [false, true]) {
	test.each([false, true])(
		`全局刷新=${globalRefresh}，首次 pending/已有缓存=%p，快新响应先到而晚旧 Promise 不回写`,
		async (cached) => {
			const client = new QueryClient({
				defaultOptions: { queries: { retry: false } },
			});
			const options = marketSessionQuery("CN", "510300.SS");
			const old = Promise.withResolvers<string>();
			const signals: AbortSignal[] = [];
			let calls = 0;
			if (cached) client.setQueryData(options.queryKey, "cached");
			const observer = new QueryObserver(client, {
				...options,
				queryFn: ({ signal }) => {
					signals.push(signal);
					return ++calls === 1 ? old.promise : Promise.resolve("fresh");
				},
			});
			const unsubscribe = observer.subscribe(() => {});
			try {
				await (globalRefresh
					? refreshWithMarketSession(client)
					: refreshMarketSession(client, options.queryKey));
				expect(calls).toBe(2);
				expect(signals[0]?.aborted).toBe(true);
				expect(client.getQueryData(options.queryKey)).toBe("fresh");
				old.resolve("late-old");
				await old.promise;
				await Promise.resolve();
				expect(client.getQueryData(options.queryKey)).toBe("fresh");
			} finally {
				old.resolve("late-old");
				unsubscribe();
				client.clear();
			}
		},
	);
}

test.each(["CN:510050.SS", "US:AAPL", "CN:510300.SS"])(
	"关闭旧观察者后切换/重开 %s，旧请求被取消且晚响应不污染",
	async (target) => {
		const client = new QueryClient();
		const old = Promise.withResolvers<string>();
		let signal: AbortSignal | undefined;
		const first = marketSessionQuery("CN", "510300.SS");
		const observer = new QueryObserver(client, {
			...first,
			queryFn: (context) => {
				signal = context.signal;
				return old.promise;
			},
		});
		const stop = observer.subscribe(() => {});
		stop();
		const [market = "CN", symbol = "510300.SS"] = target.split(":");
		const second = marketSessionQuery(market, symbol);
		const next = new QueryObserver(client, {
			...second,
			queryFn: () => Promise.resolve("new-detail"),
		});
		const stopNext = next.subscribe(() => {});
		try {
			await next.refetch();
			expect(signal?.aborted).toBe(true);
			old.resolve("old-detail");
			await old.promise;
			await Promise.resolve();
			expect(next.getCurrentResult().data).toBe("new-detail");
			expect(client.getQueryData(second.queryKey)).toBe("new-detail");
		} finally {
			old.resolve("old-detail");
			stopNext();
			client.clear();
		}
	},
);
