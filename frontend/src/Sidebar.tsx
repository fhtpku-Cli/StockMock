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
		subtitle: "持仓以原币计价，总资产折合人民币。",
	},
	{
		id: "watchlist",
		label: "自选",
		icon: Star,
		subtitle: "查看自选，也可按市场或代码发现标的。",
	},
	{
		id: "orders",
		label: "委托",
		icon: ArrowsLeftRight,
		subtitle: "查看成交结果和仍在等待的委托。",
	},
	{
		id: "journal",
		label: "复盘",
		icon: Notebook,
		subtitle: "对照计划与实际交易，记录本次结论。",
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
				<span>持仓练习</span>
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
