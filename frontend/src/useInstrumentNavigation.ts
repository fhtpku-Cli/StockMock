import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { type Instrument, instrumentSchema } from "./schema";

export type SymbolError = {
	readonly symbol: string;
	readonly message: string;
} | null;

export function useInstrumentNavigation(
	onSelect: (instrument: Instrument) => void,
) {
	const request = useRef<AbortController | null>(null);
	const [error, setError] = useState<SymbolError>(null);
	const cancel = useCallback(() => {
		request.current?.abort();
		request.current = null;
		setError(null);
	}, []);
	useEffect(() => () => request.current?.abort(), []);
	const open = async (symbol: string) => {
		cancel();
		const controller = new AbortController();
		request.current = controller;
		try {
			const instrument = await api(
				`instruments/${encodeURIComponent(symbol)}`,
				instrumentSchema,
				undefined,
				{ signal: controller.signal },
			);
			if (request.current === controller && !controller.signal.aborted)
				onSelect(instrument);
		} catch (cause) {
			if (request.current === controller && !controller.signal.aborted)
				setError({ symbol, message: errorMessage(cause) });
		}
	};
	return { open, cancel, error };
}
