import { type QueryClient, queryOptions } from "@tanstack/react-query";
import { api } from "./api";
import { marketSessionSchema } from "./marketSessionSchema";

export function marketSessionQuery(market: string, symbol: string) {
	return queryOptions({
		queryKey: ["market-session", market, symbol],
		queryFn: ({ signal }) =>
			api(
				`markets/${encodeURIComponent(market)}/session`,
				marketSessionSchema.refine((value) => value.market === market),
				undefined,
				{ signal },
			),
		staleTime: 0,
		retry: false,
		refetchOnMount: "always",
		refetchOnWindowFocus: "always",
		refetchOnReconnect: "always",
		refetchInterval: 30000,
		refetchIntervalInBackground: false,
	});
}

export function refreshMarketSession(
	client: QueryClient,
	queryKey: readonly string[],
) {
	// resetQueries 同时取消首次 pending 与有缓存的请求，晚旧 Promise 不能回写。
	return client.resetQueries({ queryKey, exact: true });
}

export async function refreshWithMarketSession(
	client: QueryClient,
): Promise<void> {
	await Promise.all([
		client.resetQueries({ queryKey: ["market-session"], type: "active" }),
		client.invalidateQueries({
			predicate: ({ queryKey }) => queryKey[0] !== "market-session",
		}),
	]);
}
