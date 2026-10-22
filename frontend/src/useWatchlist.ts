import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api, removeWatch } from "./api";
import type { Instrument } from "./schema";
import { acknowledgmentSchema, instrumentSchema } from "./schema";

export function useWatchlist() {
	const client = useQueryClient();
	const watch = useQuery({
		queryKey: ["watchlist"],
		queryFn: () => api("watchlist", z.array(instrumentSchema)),
	});
	const contains = (symbol: string) =>
		watch.data?.some((item) => item.symbol === symbol) ?? false;
	const toggle = useMutation({
		mutationFn: async (instrument: Instrument) => {
			if (contains(instrument.symbol)) await removeWatch(instrument.symbol);
			else
				await api("watchlist", acknowledgmentSchema, {
					symbol: instrument.symbol,
				});
		},
		onSuccess: () => client.invalidateQueries({ queryKey: ["watchlist"] }),
	});
	return {
		watch,
		toggle,
		contains,
		pending: toggle.isPending || !watch.isSuccess,
	};
}
