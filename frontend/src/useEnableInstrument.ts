import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";
import { api, errorMessage } from "./api";
import type { Instrument } from "./schema";
import { instrumentSchema } from "./schema";

export function useEnableInstrument() {
	const client = useQueryClient();
	const request = useRef<AbortController | null>(null);
	const cancel = useCallback(() => {
		request.current?.abort();
		request.current = null;
	}, []);
	useEffect(() => cancel, [cancel]);
	const verify = async (
		symbol: string,
		onEnabled: (item: Instrument) => void,
		onError: (message: string) => void,
	) => {
		if (request.current) return;
		const controller = new AbortController();
		request.current = controller;
		try {
			const instrument = await api(
				`instruments/${encodeURIComponent(symbol)}/enable`,
				instrumentSchema,
				{},
				{ timeout: 120_000, signal: controller.signal },
			);
			if (request.current !== controller) return;
			if (instrument.symbol !== symbol || !instrument.tradable) {
				onError(
					instrument.restriction ||
						"暂时无法核实该证券的交易资格，请重新搜索。",
				);
				return;
			}
			const keys = ["instruments", "instrument-search", "watchlist"];
			await Promise.all(
				keys.map((key) => client.cancelQueries({ queryKey: [key] })),
			);
			if (request.current !== controller) return;
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
			void client.invalidateQueries({ queryKey: ["quote", symbol] });
			onEnabled(instrument);
		} catch (error) {
			if (request.current === controller) onError(errorMessage(error));
		} finally {
			if (request.current === controller) request.current = null;
		}
	};
	return { verify, cancel };
}
