import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect } from '@playwright/test';
import { launchBrowser } from './browser.mjs';
import { url, output, password, fixture, overview, selected, rows, detail, openCard, assertSelected, assertLayout, fillDraft, assertDraft, enterReviews } from './review-orders-support.mjs';

// Given: 既有E1播种器生成的全新隔离受控历史；不是旧HTTP样本或真实账户。
const browser = await launchBrowser();
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const observations = [];
const requests = [];
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  if (new URL(request.url()).pathname.startsWith('/api/')) requests.push({ method: request.method(), path: new URL(request.url()).pathname });
});
const folded = card => selected(card).locator('.order-row details');
async function capture(name, facts) {
  const image = resolve(output, `${name}.png`);
  const text = resolve(output, `${name}.txt`);
  await page.screenshot({ path: image });
  await writeFile(text, await page.locator('body').innerText());
  observations.push({ name, facts, image, text, passed: true });
}
let passed = false;
try {
  await page.goto(url);
  await page.getByLabel('访问密码').fill(password);
  await page.getByRole('button', { name: '进入我的空间', exact: true }).click();
  await expect(page.getByRole('heading', { name: '资产概览', exact: true })).toBeVisible();
  const a = await detail(context, 'a');
  await enterReviews(page);
  const browsingStart = requests.length;
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    let card = await openCard(page, 'a');
    // When: 打开有版本修改和部分卖出的计划。
    await expect(folded(card)).not.toHaveAttribute('open');
    await expect(selected(card).getByText('历史记录未保存完整行情依据。', { exact: true }).first()).toBeVisible();
    await expect(selected(card).getByRole('region', { name: '下单依据对照' })).toBeVisible();
    const target = selected(card).locator('.comparison-row').filter({ hasText: '目标参考价' });
    await expect(target.locator('p')).toHaveText(['10', '11']);
    await expect(card).toContainText('198.90');
    await expect(card).toContainText('部分卖出不代表计划最终结果');
    await selected(card).scrollIntoViewIfNeeded();
    await assertLayout(page, card);
    await capture(`reading-a-default-${width}`, { defaultClosed: true, comparison: '10→11', warningVisible: true });
    // When: 键盘展开原始依据，再切到卖单。
    const summary = folded(card).locator('summary');
    await summary.focus();
    await page.keyboard.press('Enter');
    await expect(folded(card)).toHaveAttribute('open');
    await expect(folded(card).getByText('E1受控：A第一版', { exact: true })).toBeVisible();
    for (const label of ['生效时间（北京时间）', '有效至', '成交时间 / 确认时间', '交易理由', '目标 / 止损参考价']) await expect(folded(card).getByText(label, { exact: true })).toBeVisible();
    await fillDraft(card, '阅读减负草稿');
    const sellButton = rows(card).nth(1).getByRole('button');
    await sellButton.focus();
    await page.keyboard.press('Enter');
    await assertSelected(card, a.orders[1].id);
    await expect(sellButton).toBeFocused();
    await expect(folded(card)).not.toHaveAttribute('open');
    await assertDraft(card, '阅读减负草稿');
    await folded(card).locator('summary').focus();
    await page.keyboard.press('Space');
    await expect(folded(card)).toHaveAttribute('open');
    await expect(folded(card).getByText('E1受控：A第二版', { exact: true })).toBeVisible();
    await expect(folded(card).getByText('E1受控：A第一版', { exact: true })).toHaveCount(0);
    await capture(`reading-a-switch-${width}`, { keyboard: 'Enter/Space', resetOnSwitch: true, focusRetained: true, draftRetained: true, fullEvidenceReachable: true });
    await openCard(page, 'other');
    card = await openCard(page, 'a');
    await assertSelected(card, a.orders[0].id);
    await expect(folded(card)).not.toHaveAttribute('open');
    await expect(card.getByLabel('本次结论', { exact: true })).toHaveValue('');
    await capture(`reading-subject-reset-${width}`, { resetOnReturn: true });
    await card.getByRole('button', { name: '收起复盘详情', exact: true }).click();
    await page.getByRole('button', { name: width === 1440 ? /个人账户/ : '账户设置', exact: width !== 1440 }).click();
    await page.getByRole('button', { name: '查看历史', exact: true }).click();
    const archive = page.getByRole('dialog', { name: '已归档练习 · 只读' });
    await archive.getByLabel('复盘状态', { exact: true }).selectOption('all');
    card = await openCard(page, 'b');
    await expect(folded(card)).not.toHaveAttribute('open');
    await expect(card).toContainText('已归档 · 只读');
    await expect(rows(card)).toContainText('已过期');
    await expect(rows(card)).toContainText('尚未成交');
    await expect(card).toContainText('尚不能确认');
    await expect(card.locator('form')).toHaveCount(0);
    for (const name of ['结束本计划', '保存普通笔记', '保存本次复盘结论']) await expect(card.getByRole('button', { name, exact: true })).toHaveCount(0);
    await folded(card).locator('summary').click();
    await expect(folded(card).getByText('撤单请求时间', { exact: true })).toBeVisible();
    await expect(rows(card)).not.toContainText('已撤单');
    await assertLayout(page, card);
    await capture(`reading-archive-${width}`, { readOnly: true, expiredNotCancelled: true, missingPnlNotZero: true, fullEvidenceReachable: true });
    await archive.getByRole('button', { name: '关闭', exact: true }).click();
    await page.getByRole('dialog', { name: '练习设置', exact: true }).getByRole('button', { name: '关闭', exact: true }).click();
    await enterReviews(page);
  }
  const card = await openCard(page, 'a');
  await fillDraft(card, '同笔刷新草稿');
  await folded(card).locator('summary').click();
  // When: 使用既有冲突刷新入口，以真实详情GET刷新同一笔（仅POST回执模拟409）。
  const pattern = `**/api/reviews/plan/${fixture.subjects.a.id}/conclusions`;
  await page.route(pattern, route => route.fulfill({ status: 409, json: { detail: { code: 'review_conflict', message: '受控冲突，触发同笔刷新' } } }));
  await card.getByRole('button', { name: '保存本次复盘结论', exact: true }).click();
  await card.getByRole('button', { name: '刷新事实并保留草稿', exact: true }).click();
  await expect(card.getByRole('button', { name: '刷新事实并保留草稿', exact: true })).toHaveCount(0);
  await expect(folded(card)).toHaveAttribute('open');
  await assertDraft(card, '同笔刷新草稿');
  await capture('reading-same-order-refresh', { realDetailGet: true, simulatedPost409: true, expandedAndDraftRetained: true });
  await page.unroute(pattern);
  // Then: 除上述明确拦截的POST外，浏览期间只有GET，没有业务数据写入。
  const writes = requests.slice(browsingStart).filter(request => request.method !== 'GET');
  expect(writes).toEqual([{ method: 'POST', path: `/api/reviews/plan/${fixture.subjects.a.id}/conclusions` }]);
  expect(errors).toEqual([]);
  passed = true;
} catch (error) {
  await capture('reading-failure', { error: String(error) });
  throw error;
} finally {
  await writeFile(resolve(output, 'reading-results.json'), JSON.stringify({ passed, source: fixture.source, observations, requests, errors }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ passed, scenarios: observations.length, output }));
