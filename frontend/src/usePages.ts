import { useInfiniteQuery } from "@tanstack/react-query";
import type { z } from "zod";
import { api } from "./api";
import { pageSchema } from "./planSchema";

export function usePages<T extends z.ZodType>(
	key: readonly string[],
	path: string,
	item: T,
	enabled = true,
) {
	const schema = pageSchema(item);
	const query = useInfiniteQuery({
		queryKey: key,
		initialPageParam: 0,
		queryFn: ({ pageParam }) =>
			api(
				`${path}${path.includes("?") ? "&" : "?"}offset=${pageParam}&limit=25`,
				schema,
			),
		getNextPageParam: (last) =>
			last.offset + last.items.length < last.total
				? last.offset + last.items.length
				: undefined,
		enabled,
	});
	return {
		...query,
		items: query.data?.pages.flatMap((page) => page.items) ?? [],
		total: query.data?.pages[0]?.total ?? 0,
	};
}
