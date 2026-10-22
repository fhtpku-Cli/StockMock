export function Pagination({
	count,
	total,
	pending,
	more,
	onMore,
}: {
	readonly count: number;
	readonly total: number;
	readonly pending: boolean;
	readonly more: boolean;
	readonly onMore: () => void;
}) {
	return (
		<div className="list-pagination">
			<span className="footnote">
				已显示 {count} / {total} 条
			</span>
			{more && (
				<button
					type="button"
					className="button small"
					disabled={pending}
					onClick={onMore}
				>
					{pending ? "加载中…" : "加载更多"}
				</button>
			)}
		</div>
	);
}
