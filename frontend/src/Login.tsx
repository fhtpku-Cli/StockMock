import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ChartBar, LockKey, ArrowRight } from "@phosphor-icons/react";
import { api, errorMessage } from "./api";
import { acknowledgmentSchema } from "./schema";
import { ErrorNotice } from "./ui";
export function Login({
	configured,
	expired = false,
	onSuccess,
}: {
	readonly configured: boolean;
	readonly expired?: boolean;
	readonly onSuccess: () => void;
}) {
	const [password, setPassword] = useState("");
	const login = useMutation({
		mutationFn: () => api("auth/login", acknowledgmentSchema, { password }),
		onSuccess,
	});
	return (
		<main className="login-layout">
			<section className="login-story">
				<div className="brand">
					<span className="brand-icon">
						<ChartBar weight="fill" size={24} />
					</span>
					<span>持仓练习</span>
				</div>
				<div>
					<span className="eyebrow">个人模拟投资</span>
					<h1>
						认真做判断，
						<br />
						从容看结果。
					</h1>
					<p>
						用虚拟资金练习真实决策。
						<br />
						观察市场、记录买卖，让每一次经验都有迹可循。
					</p>
					<div className="login-principles">
						<span>100 万元起始本金</span>
						<span>A 股 · 港股 · 美股 · ETF</span>
						<span>属于自己的投资记录</span>
					</div>
				</div>
				<small>不涉及真实资金 · 不构成投资建议</small>
			</section>
			<section className="login-form">
				<span className="login-lock">
					<LockKey size={28} />
				</span>
				<h2>回到你的投资空间</h2>
				<p>
					{expired
						? "登录已失效，请重新输入密码。账户记录仍保留在服务端。"
						: "输入私有访问密码，继续这一轮练习。"}
				</p>
				{!configured ? (
					<ErrorNotice message="当前空间尚未配置访问密码，请联系本机管理员完成初始化。" />
				) : (
					<form
						onSubmit={(e) => {
							e.preventDefault();
							login.mutate();
						}}
					>
						<div className="field">
							<label htmlFor="password">访问密码</label>
							<input
								id="password"
								name="password"
								type="password"
								autoComplete="current-password"
								required
								value={password}
								onChange={(e) => setPassword(e.target.value)}
								disabled={login.isPending}
							/>
						</div>
						{login.isError && (
							<ErrorNotice message={errorMessage(login.error)} />
						)}
						<button
							type="submit"
							className="button primary full"
							disabled={login.isPending}
						>
							{login.isPending ? "正在进入…" : "进入我的空间"}
							<ArrowRight size={18} />
						</button>
					</form>
				)}
				<p className="footnote">
					<LockKey size={14} />
					私有空间，不开放注册。
				</p>
			</section>
		</main>
	);
}
