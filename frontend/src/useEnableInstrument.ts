import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { api } from "./api";
import { instrumentSchema } from "./schema";
import type { Instrument } from "./schema";

export function useEnableInstrument(
	onEnabled: (instrument: Instrument) => void,
) {
	const client = useQueryClient();
	const submitting = useRef(false);
	const mutation = useMutation({
		mutationFn: (symbol: string) =>
			api(
				`instruments/${encodeURIComponent(symbol)}/enable`,
				instrumentSchema,
				{},
				{ timeout: 120_000 },
			),
		onSuccess: async (instrument) => {
			const keys = ["instruments", "instrument-search", "watchlist"];
			await Promise.all(
				keys.map((key) => client.cancelQueries({ queryKey: [key] })),
			);
			client.setQueryData<Instrument[]>(["instruments"], (items) =>
				items
					? [
							...items.filter((item) => item.symbol !== instrument.symbol),
							instrument,
						]
					: undefined,
			);
			client.setQueryData<Instrument[]>(["watchlist"], (items) =>
				items?.map((item) =>
					item.symbol === instrument.symbol ? instrument : item,
				),
			);
			client.setQueriesData<{
				items: Instrument[];
				source: string;
				message: string;
			}>({ queryKey: ["instrument-search"] }, (result) =>
				result
					? {
							...result,
							items: result.items.map((item) =>
								item.symbol === instrument.symbol ? instrument : item,
							),
						}
					: undefined,
			);
			onEnabled(instrument);
			void client.invalidateQueries({ queryKey: ["quote", instrument.symbol] });
		},
		onSettled: () => {
			submitting.current = false;
		},
	});
	return {
		...mutation,
		mutate: (symbol: string) => {
			if (submitting.current) return;
			submitting.current = true;
			mutation.mutate(symbol);
		},
	};
}
