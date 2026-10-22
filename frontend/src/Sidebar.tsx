import {
	ArrowsLeftRight,
	ChartBar,
	ChartPieSlice,
	GearSix,
	LockKey,
	Notebook,
	Star,
} from "@phosphor-icons/react";
export const navigation = [
	{
		id: "assets",
		label: "资产",
		icon: ChartPieSlice,
		subtitle: "看清全局，专注每一次决策。",
	},
	{
		id: "watchlist",
		label: "自选",
		icon: Star,
		subtitle: "从熟悉的标的，建立自己的观察清单。",
	},
	{
		id: "orders",
		label: "委托",
		icon: ArrowsLeftRight,
		subtitle: "每一笔计划，都有清楚的来路与去向。",
	},
	{
		id: "journal",
		label: "复盘",
		icon: Notebook,
		subtitle: "记录判断，让经验慢慢积累。",
	},
] as const;
export type Page = (typeof navigation)[number]["id"];

export function Sidebar({
	page,
	setPage,
	onSettings,
}: {
	readonly page: Page;
	readonly setPage: (page: Page) => void;
	readonly onSettings: () => void;
}) {
	return (
		<aside className="sidebar">
			<button
				type="button"
				className="brand brand-button"
				onClick={() => setPage("assets")}
			>
				<span className="brand-icon">
					<ChartBar weight="fill" size={24} />
				</span>
				<span>
					持仓练习<small>让判断慢慢生长</small>
				</span>
			</button>
			<div className="workspace-label">我的投资空间</div>
			<nav aria-label="主导航">
				{navigation.map((n) => (
					<button
						type="button"
						key={n.id}
						className={page === n.id ? "nav-item active" : "nav-item"}
						aria-current={page === n.id ? "page" : undefined}
						onClick={() => setPage(n.id)}
					>
						<n.icon size={21} weight={page === n.id ? "fill" : "regular"} />
						<span>{n.label}</span>
						{page === n.id && <span className="nav-dot" />}
					</button>
				))}
			</nav>
			<div className="sidebar-bottom">
				<div className="private-note">
					<LockKey size={16} />
					<span>仅属于你的练习</span>
				</div>
				<button
					type="button"
					className="account-button"
					onClick={() => onSettings()}
				>
					<span className="avatar">我</span>
					<span>
						个人账户<small>模拟投资 · 人民币本位</small>
					</span>
					<GearSix size={20} />
				</button>
			</div>
		</aside>
	);
}
