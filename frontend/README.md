# 持仓练习前端

React、TypeScript、Vite 与 Bun 构建的个人模拟投资界面。四个主入口为资产、自选、委托、复盘；证券详情、交易预览、现金换汇、轮次归档在二级面板中完成。所有账户与行情来自本地后端，不使用失败后替换的演示数据。

## 本地启动

从项目根目录执行：

```bash
cd frontend
bun install --frozen-lockfile
bun run build
cd ..
./scripts/start-local.sh
```

在浏览器打开 `http://127.0.0.1:8000`。首次启动脚本会引导设置私有密码；密码不应写进报告或提交到仓库。需要修改密码时执行 `./scripts/start-local.sh --set-password`。后端必须保持运行才能继续处理委托。

开发界面时，可在后端运行后另开终端，在 `frontend/` 执行 `bun run dev`，访问 `http://127.0.0.1:5173`。生产预览也使用 `bun run preview` 的 5173 端口；不要随意改成未被后端允许的跨站端口。

## 验证

```bash
cd frontend
bun run build
bun run test
bun run test:browser
bun tests/live-order.mjs
bun tests/price-precision.mjs
```

`test` 运行四个真实断言的边界与精度测试，不是空测试命令。`test:browser` 使用真实本地服务，会新增明确标记为本地验收的复盘记录，并验证自选增删、导出和异常状态。`live-order.mjs` 只允许在中国市场非交易时段运行，会提交一笔明确标记的模拟预挂委托并立即撤单；不证明真实盘中成交。

隔离交易验证需另开终端，从根目录执行：

```bash
backend/.venv/bin/python frontend/tests/controlled_server.py
```

然后在 `frontend/` 执行 `bun run test:trading`。隔离服务使用 8001 端口、临时 SQLite 和标记为 `controlled-test` 的行情，通过实际交易引擎测试委托预览、受理、占用、撤单、关联复盘及归档后读取。关闭隔离服务即清理临时数据库。

浏览器脚本默认访问 `http://127.0.0.1:8000`，可通过 `FRONTEND_TEST_URL` 修改；隔离交易脚本固定访问 8001。浏览器优先使用 `CHROME_PATH` 指定路径，其次使用 macOS 已安装的 Chrome，否则使用 Playwright 安装的 Chromium（可先执行 `bunx playwright install chromium`）。证据保存在项目私有的 `.omo/evidence/frontend/`，不放入公开站点。

## 维护约定

A 股与美股的在线搜索结果可以在详情申请启用模拟交易。前端只在后端返回核验通过的证券资料后更新详情、搜索、自选和常用清单；核验失败仍仅观察并显示可重试原因，港股不提供启用入口。核验请求最多等待 120 秒，等待期间防止重复点击。买入数量使用后端的起购数量和递增步长，科创板显示 200 股起、递增 1 股；卖出仍由可卖持仓及预览判断。

扩围界面验证从项目根目录执行：

```bash
cd frontend
bun run build --outDir ../.omo/evidence/tradability-ui/site
bun tests/tradability.mjs
```

脚本启动独立的 5197 前端预览与 8027 后端，使用随机测试口令和临时 SQLite，结束时自动关闭。真实启用核验、规则、持久化、数量计算与预览接口保留；只有外部证券身份、行情和时钟受控。覆盖 Chrome/WebKit 的桌面、390px、320px，核验等待及双击、失败重试、成功响应丢失后重试、详情/搜索/自选同步、刷新保留、港股观察与日期筛选回归。证据保存在 `.omo/evidence/tradability-ui/`，不代表真实行情或手机真机已验收。

- `src/schema.ts` 验证外部返回结构；`src/api.ts` 统一请求超时、错误、金额和价格格式。资金金额保留两位，证券价格按最小价位保留精度。
- 资金计算、下单校验、成交判断由后端执行；前端不计算可用余额或制造成交。
- `DESIGN.md` 定义设计方向，`src/styles/` 按基础、布局、组件和响应式拆分；源代码通过 Biome 保持可读。
- 所有修改后至少执行构建与对应行为测试。格式化命令：`bunx biome format --write src tests vite.config.ts package.json tsconfig.json`。
- PWA 清单只提供安装入口，不缓存账户数据、不离线提交交易。手机外网访问与真实设备安装需独立验证。

## 完整个人功能验证

当前目录支持市场、股票/ETF、可交易状态及代码名称筛选。订单数量 25%/50%/100% 由服务端考虑费用、币种与占用计算，必须继续预览才能提交。设置内可登记税前现金分红、应收股息到账及整数拆并股；账户与归档均可查看记录。资产历史按实际日期展示，不连过缺失、过时或未采集日期。计划持有期可写在交易理由或复盘中，本轮不增加独立字段或自动止盈止损。

新增隔离浏览器场景使用临时 SQLite 与显式种子持仓；不会改写真实账户。先在项目根目录启动测试服务，再在另一终端执行浏览器脚本：

```bash
backend/.venv/bin/python frontend/tests/personal_server.py
# 另一终端，从项目根目录运行
cd frontend
bun tests/personal.mjs
```

默认 `8002`，测试完成后 Ctrl+C 停止，临时库自动清理。测试行情固定在 2026-10-22 北京时间 08:00，上一交易日报价固定为 2026-10-21；不得将此数据用于真实行情验收。

真实本机只读检查使用 `bun tests/personal-live.mjs`，默认访问 `http://127.0.0.1:8000`，通过 `FRONTEND_TEST_URL` 可选择同源入口。它验证真实目录、限制提示与手机布局，不提交交易或调整。两种浏览器脚本使用系统 Chrome（可设 `CHROME_PATH`）或已安装的 Playwright Chromium。测试证据写入私有 `.omo/evidence/frontend-personal/`。
