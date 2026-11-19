import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { portfolioSchema, quoteSchema } from "./schema";

export function useTradeReference(symbol: string) {
	const portfolio = useQuery({
		queryKey: ["portfolio"],
		queryFn: () => api("portfolio", portfolioSchema),
	});
	const quote = useQuery({
		queryKey: ["quote", symbol],
		queryFn: () =>
			api(`instruments/${encodeURIComponent(symbol)}/quote`, quoteSchema),
	});
	return { portfolio, quote };
}
