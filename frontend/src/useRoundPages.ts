import { useInfiniteQuery } from "@tanstack/react-query";
import { z } from "zod";
import { ApiError, api } from "./api";
import { pageSchema } from "./planSchema";
export function useRoundPages<T extends z.ZodType>(
	key: readonly string[],
	path: string,
	item: T,
) {
	const schema = pageSchema(item).extend({
		round_id: z.string(),
		read_only: z.boolean(),
	});
	const query = useInfiniteQuery({
		queryKey: key,
		initialPageParam: { offset: 0, roundId: "" },
		queryFn: async ({ pageParam }) => {
			const page = await api(
				`${path}${path.includes("?") ? "&" : "?"}offset=${pageParam.offset}&limit=50`,
				schema,
			);
			if (pageParam.roundId && page.round_id !== pageParam.roundId)
				throw new ApiError(
					"round_changed",
					"练习轮次已变化，请刷新列表；旧轮记录不会拼入新轮。",
				);
			return page;
		},
		getNextPageParam: (last) =>
			last.offset + last.items.length < last.total
				? { offset: last.offset + last.items.length, roundId: last.round_id }
				: undefined,
	});
	return {
		...query,
		items: query.data?.pages.flatMap((page) => page.items) ?? [],
		total: query.data?.pages[0]?.total ?? 0,
		roundId: query.data?.pages[0]?.round_id,
	};
}
