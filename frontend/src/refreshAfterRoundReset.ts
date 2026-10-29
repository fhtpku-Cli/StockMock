import type { QueryClient } from "@tanstack/react-query";

export async function refreshAfterRoundReset(
	client: QueryClient,
): Promise<void> {
	await Promise.all(
		["portfolio", "orders"].map((key) =>
			client.resetQueries({ queryKey: [key], exact: true }),
		),
	);
	await client.invalidateQueries({
		predicate: ({ queryKey }) =>
			queryKey[0] !== "portfolio" && queryKey[0] !== "orders",
	});
}
