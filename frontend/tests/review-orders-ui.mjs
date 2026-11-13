import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect } from '@playwright/test';
import { launchBrowser } from './browser.mjs';
import { url, output, password, fixture, observations, overview, selected, rows, api, detail, openCard, capture, assertSelected, assertLayout, fillDraft, assertDraft, enterReviews } from './review-orders-support.mjs';
import { refreshScenarios, coverageScenario } from './review-orders-refresh.mjs';

const browser = await launchBrowser();
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const requests = [];
const errors = [];
page.on('request', request => {
  const requestUrl = new URL(request.url());
  if (requestUrl.pathname.startsWith('/api/')) requests.push({ method: request.method(), path: requestUrl.pathname });
});
page.on('pageerror', error => errors.push(String(error)));
let passed = false;
try {
  await expect.poll(async () => {
    try { return (await context.request.get(url, { timeout: 2000 })).status(); }
    catch { return 0; }
  }, { timeout: 15000 }).toBe(200);
  await page.goto(url);
  await page.getByLabel('访问密码').fill(password);
  await page.getByRole('button', { name: '进入我的空间', exact: true }).click();
  await expect(page.getByRole('heading', { name: '资产概览', exact: true })).toBeVisible();
  const raw = {};
  for (const label of Object.keys(fixture.subjects)) {
    raw[label] = await detail(context, label);
    expect(raw[label].orders.map(order => order.id).sort()).toEqual([...fixture.subjects[label].orders].sort());
  }
  expect(raw.a.review).toMatchObject({ bought_quantity: 200, sold_quantity: 100, remaining_quantity: 100, account_quantity: 100, realized_pnl: '198.90' });
  expect(raw.b.read_only).toBe(true);
  await writeFile(resolve(output, 'http-details.json'), JSON.stringify(raw, null, 2));
  await enterReviews(page);
  const browsingStart = requests.length;
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    let card = await openCard(page, 'a');
    await expect(rows(card)).toHaveCount(2);
    await expect(card.getByRole('button', { name: '结束本计划', exact: true })).toBeVisible();
    await card.getByRole('button', { name: '结束本计划', exact: true }).click();
    await expect(card).toContainText('未完成的委托不会自动撤销');
    await card.getByRole('button', { name: '继续保留', exact: true }).click();
    await expect(card.getByRole('button', { name: '结束本计划', exact: true })).toBeVisible();
    for (const order of raw.a.orders) {
      const row = overview(card).locator(`[data-review-order-id="${order.id}"]`);
      await expect(row).toContainText('已成交');
      await expect(row).toContainText(`${order.side === 'buy' ? '买入' : '卖出'} ${order.quantity}`);
      await expect(row).toContainText(`第 ${order.plan_snapshot.version} 版`);
      await expect(row).toContainText('2026-10-23');
      await expect(row).toContainText(order.fill_price);
      await expect(row.locator('dl > div').filter({ hasText: '市场成交时间' })).toContainText(order.side === 'buy' ? '10:05:00' : '10:10:00');
    }
    await assertSelected(card, fixture.subjects.a.orders[0]);
    const comparison = selected(card).getByRole('region', { name: '下单依据对照' });
    await expect(comparison).toContainText('下单时第 1 版 · 当前第 2 版');
    const target = comparison.locator('.comparison-row').filter({ hasText: '目标参考价' });
    await expect(target.locator('p')).toHaveText(['10', '11']);
    await expect(card).toContainText('198.90');
    await expect(card).toContainText('部分卖出不代表计划最终结果');
    const headings = await card.locator('h3').allTextContents();
    expect(headings.indexOf('关联委托概览（2 笔）')).toBeLessThan(headings.findIndex(value => value.startsWith('历次正式结论')));
    await fillDraft(card, '仅浏览草稿');
    const first = rows(card).first().getByRole('button');
    const second = rows(card).nth(1).getByRole('button');
    await first.focus();
    await page.keyboard.press('Tab');
    await expect(second).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(second).toBeFocused();
    await assertSelected(card, fixture.subjects.a.orders[1]);
    await expect(selected(card).getByRole('region', { name: '下单依据对照' })).toContainText('下单时第 2 版 · 当前第 2 版');
    await assertDraft(card, '仅浏览草稿');
    await overview(card).scrollIntoViewIfNeeded();
    await assertLayout(page, card);
    await capture(page, `a-keyboard-${width}`, { completeSummary: true, targetPrice: '10→11', pnl: '198.90', retainedFocus: true, draft: '三个字段保留', width });
    card = await openCard(page, 'other');
    await expect(rows(card)).toHaveCount(1);
    await assertSelected(card, fixture.subjects.other.orders[0]);
    await expect(card).not.toContainText('A第二版');
    await capture(page, `same-symbol-ownership-${width}`, { realHTTP: true, ids: fixture.subjects.other.orders });
    card = await openCard(page, 'a');
    await assertSelected(card, fixture.subjects.a.orders[0]);
    await expect(card.getByLabel('本次结论', { exact: true })).toHaveValue('');
    await capture(page, `cached-subject-return-${width}`, { resetSelection: true, priorDraftNotLeaked: true });
    for (const label of ['empty', 'single', 'legacy', 'mixed']) {
      card = await openCard(page, label);
      await expect(rows(card)).toHaveCount(fixture.subjects[label].orders.length);
      if (label === 'empty') {
        await expect(overview(card)).toContainText('尚无关联委托，也可以主动记录观察结论');
        await expect(card.getByLabel('本次结论', { exact: true })).toBeVisible();
      } else await assertSelected(card, fixture.subjects[label].orders[0]);
      if (label === 'legacy') {
        await expect(overview(card)).toContainText('当时计划未保存');
        await expect(selected(card)).toContainText('不能用当前计划补造历史');
      }
      if (label === 'mixed') {
        for (const status of ['等待成交', '撤单确认中', '已成交', '已过期', '已撤单', '已终止']) await expect(overview(card)).toContainText(status);
        const missing = raw.mixed.orders.find(order => order.status === 'filled' && order.fill_price === null);
        const missingRow = overview(card).locator(`[data-review-order-id="${missing.id}"]`);
        await expect(missingRow).toContainText('已成交');
        await expect(missingRow).toContainText('当时计划未保存');
        expect(await missingRow.getByText('未记录', { exact: true }).count()).toBe(2);
        await expect(missingRow).not.toContainText('尚未成交');
        await expect(missingRow).not.toContainText('16:02');
        await expect(overview(card)).toContainText('10.123');
      }
      await overview(card).scrollIntoViewIfNeeded();
      await assertLayout(page, card);
      await capture(page, `${label}-${width}`, { realHTTP: true, orderCount: fixture.subjects[label].orders.length, width });
    }
    for (const label of ['many21', 'many508']) {
      card = await openCard(page, label);
      await expect(rows(card)).toHaveCount(20);
      await assertSelected(card, fixture.subjects[label].orders[0]);
      await fillDraft(card, '分页保留草稿');
      while (await overview(card).getByRole('button', { name: '加载更多', exact: true }).count()) {
        await overview(card).getByRole('button', { name: '加载更多', exact: true }).click();
        await assertSelected(card, fixture.subjects[label].orders[0]);
      }
      expect(await rows(card).evaluateAll(elements => elements.map(element => element.getAttribute('data-review-order-id')))).toEqual(fixture.subjects[label].orders);
      await rows(card).last().getByRole('button').click();
      await assertSelected(card, fixture.subjects[label].orders.at(-1));
      await assertDraft(card, '分页保留草稿');
      await rows(card).last().scrollIntoViewIfNeeded();
      await assertLayout(page, card);
      await capture(page, `${label}-complete-${width}`, { count: fixture.subjects[label].orders.length, stableFullIdOrder: true, lastSelectable: true, draftRetained: true, width });
      await openCard(page, 'single');
      card = await openCard(page, label);
      await expect(rows(card)).toHaveCount(20);
      await assertSelected(card, fixture.subjects[label].orders[0]);
      await openCard(page, 'single');
    }
    await page.getByRole('button', { name: width === 1440 ? /个人账户/ : '账户设置', exact: width !== 1440 }).click();
    await page.getByRole('button', { name: '查看历史', exact: true }).click();
    const archive = page.getByRole('dialog', { name: '已归档练习 · 只读' });
    await archive.getByLabel('复盘状态', { exact: true }).selectOption('all');
    card = await openCard(page, 'b');
    await expect(rows(card)).toHaveCount(1);
    await expect(rows(card)).toContainText('已过期');
    await expect(rows(card)).toContainText('尚未成交');
    await expect(rows(card)).not.toContainText('已撤单');
    await expect(rows(card).getByText('成交价格 · CNY', { exact: true })).toHaveCount(0);
    for (const name of ['保存本次复盘结论', '保存普通笔记', '结束本计划']) await expect(card.getByRole('button', { name, exact: true })).toHaveCount(0);
    await expect(selected(card)).toContainText('撤单');
    await overview(card).scrollIntoViewIfNeeded();
    await assertLayout(page, card);
    await capture(page, `archive-b-${width}`, { readOnly: true, cancelledRequest: true, terminal: 'expired', realHTTP: true, width });
    await archive.getByRole('button', { name: '关闭', exact: true }).click();
    await page.getByRole('dialog', { name: '练习设置', exact: true }).getByRole('button', { name: '关闭', exact: true }).click();
    await enterReviews(page);
    card = await openCard(page, 'a');
    await assertSelected(card, fixture.subjects.a.orders[0]);
    await expect(card.getByRole('button', { name: '结束本计划', exact: true })).toBeVisible();
    await card.getByRole('button', { name: '收起复盘详情', exact: true }).click();
  }
  expect(requests.slice(browsingStart).filter(request => request.method !== 'GET')).toEqual([]);
  await capture(page, 'browse-without-writes', { nonGetRequests: 0, source: '浏览选择、分页、主体和归档切换的实际网络记录' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await refreshScenarios(page, context, await openCard(page, 'a'));
  await coverageScenario(page, context, await openCard(page, 'many508'));
  expect(errors).toEqual([]);
  passed = true;
} catch (error) {
  await page.screenshot({ path: resolve(output, 'failure.png') });
  await writeFile(resolve(output, 'failure.txt'), `${String(error)}\n${await page.locator('body').innerText()}`);
  throw error;
} finally {
  await writeFile(resolve(output, 'browser-results.json'), JSON.stringify({ passed, source: fixture.source, observations, errors, requests }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ passed, scenarios: observations.length, evidence: resolve(output, 'browser-results.json') }));
