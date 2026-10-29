import { number } from "./api";
import type { Portfolio } from "./schema";

const unlockDateFormat = new Intl.DateTimeFormat("sv-SE", {
	timeZone: "Asia/Shanghai",
	year: "numeric",
	month: "2-digit",
	day: "2-digit",
	hour: "2-digit",
	minute: "2-digit",
	second: "2-digit",
	hourCycle: "h23",
});

export function SellabilityDetails({
	value,
	reserved,
}: {
	readonly value: Portfolio["positions"][number]["sellability"];
	readonly reserved: number;
}) {
	if (value?.locked_quantity === 0 && reserved === 0) return null;
	return (
		<div className="sellability-details">
			{!value ? (
				<small>解锁信息暂不可用</small>
			) : value.locked_quantity > 0 && value.next_unlock_at ? (
				<>
					<small>
						待解锁{number(value.locked_quantity)}股；下一批
						{number(value.next_unlock_quantity)}股预计于
						{unlockDateFormat.format(new Date(value.next_unlock_at))}
						（北京时间）取得卖出资格。
					</small>
					{value.next_unlock_quantity < value.locked_quantity && (
						<small>
							其余{number(value.locked_quantity - value.next_unlock_quantity)}
							股属于后续批次。
						</small>
					)}
					<small>资格以刷新后的可卖数量为准；取得资格不保证成交。</small>
				</>
			) : null}
			{reserved > 0 && (
				<small>卖单占用以委托实际终态更新为准，申请撤单不会立即释放。</small>
			)}
		</div>
	);
}
