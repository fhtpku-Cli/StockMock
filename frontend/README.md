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

- `src/schema.ts` 验证外部返回结构；`src/api.ts` 统一请求超时、错误、金额和价格格式。资金金额保留两位，证券价格按最小价位保留精度。
- 资金计算、下单校验、成交判断由后端执行；前端不计算可用余额或制造成交。
- `DESIGN.md` 定义设计方向，`src/styles/` 按基础、布局、组件和响应式拆分；源代码通过 Biome 保持可读。
- 所有修改后至少执行构建与对应行为测试。格式化命令：`bunx biome format --write src tests vite.config.ts package.json tsconfig.json`。
- PWA 清单只提供安装入口，不缓存账户数据、不离线提交交易。手机外网访问与真实设备安装需独立验证。
