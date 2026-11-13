import { expect } from '@playwright/test';
import { reviewDetailSchema, conclusionResultSchema } from '../src/reviewSchema.ts';
import { fixture, pathFor, detail, selected, overview, rows, assertSelected, fillDraft, assertDraft, capture, api } from './review-orders-support.mjs';

export async function refreshScenarios(page, context, card) {
  const entry = fixture.subjects.a;
  const original = await detail(context, 'a');
  const lastId = entry.orders.at(-1);
  await overview(card).locator(`[data-review-order-id="${lastId}"] button`).click();
  await fillDraft(card, '刷新保留草稿');
  const postPattern = `**${pathFor(entry)}/conclusions`;
  await page.route(postPattern, route => route.fulfill({ status: 409, json: { detail: { code: 'review_conflict', message: 'E1受控409：事实发生变化' } } }));
  for (const mode of ['preserve', 'remove', 'empty']) {
    await card.getByRole('button', { name: '保存本次复盘结论', exact: true }).click();
    await expect(card.getByRole('button', { name: '刷新事实并保留草稿', exact: true })).toBeVisible();
    const getPattern = `**${pathFor(entry)}?*`;
    let replacement;
    if (mode !== 'preserve') {
      const orders = mode === 'remove' ? original.orders.filter(item => item.id !== lastId) : [];
      const filled = orders.filter(item => item.status === 'filled');
      const coverage = { ...original.coverage, filled_order_ids: filled.map(item => item.id) };
      replacement = reviewDetailSchema.parse({ ...original, orders, coverage, review: {
        ...original.review, coverage, order_count: orders.length, filled_count: filled.length,
        bought_quantity: filled.filter(item => item.side === 'buy').reduce((sum, item) => sum + item.quantity, 0),
        sold_quantity: 0, remaining_quantity: filled.reduce((sum, item) => sum + item.quantity, 0), realized_pnl: null,
      } });
      await page.route(getPattern, route => route.fulfill({ status: 200, json: replacement }));
    }
    const response = page.waitForResponse(value => value.url().includes(pathFor(entry) + '?') && value.request().method() === 'GET');
    await card.getByRole('button', { name: '刷新事实并保留草稿', exact: true }).click();
    expect((await response).status()).toBe(200);
    await assertDraft(card, '刷新保留草稿');
    if (mode === 'empty') {
      await expect(rows(card)).toHaveCount(0);
      await expect(selected(card)).toHaveCount(0);
      await expect(overview(card)).toContainText('尚无关联委托，也可以主动记录观察结论');
    } else await assertSelected(card, mode === 'preserve' ? lastId : entry.orders[0]);
    await overview(card).scrollIntoViewIfNeeded();
    await capture(page, `refresh-${mode}`, { post: '仅浏览器模拟409', get: mode === 'preserve' ? '真实HTTP完整详情' : '仅浏览器替换一次详情GET，不删除数据库历史', selected: mode === 'empty' ? null : mode === 'preserve' ? lastId : entry.orders[0], threeDraftFieldsRetained: true });
    if (replacement) await page.unroute(getPattern);
  }
  await page.unroute(postPattern);
  expect((await detail(context, 'a')).orders).toEqual(original.orders);
}

export async function coverageScenario(page, context, card) {
  const entry = fixture.subjects.many508;
  const before = await detail(context, 'many508');
  expect(before.coverage.filled_order_ids).toHaveLength(508);
  await expect(rows(card)).toHaveCount(20);
  await expect(card.locator('.conclusion-form')).toContainText('508 笔已确认成交');
  const staleResponse = await context.request.post(`${page.url().replace(/\/$/, '')}${pathFor(entry)}/conclusions`, {
    headers: { Origin: page.url().replace(/\/$/, '') },
    data: { request_id: crypto.randomUUID(), round_id: entry.round_id, conclusion: '受控过期覆盖', deviation: '', next_action: '', coverage: { ...before.coverage, filled_order_ids: [] } },
  });
  expect(staleResponse.status()).toBe(409);
  await fillDraft(card, '完整508笔覆盖');
  const posted = page.waitForResponse(response => response.url().endsWith(`${pathFor(entry)}/conclusions`) && response.request().method() === 'POST');
  await card.getByRole('button', { name: '保存本次复盘结论', exact: true }).click();
  const response = await posted;
  expect(response.status()).toBe(200);
  const receipt = conclusionResultSchema.parse(await response.json());
  expect(receipt.conclusion.coverage.filled_order_ids).toHaveLength(508);
  await Bun.write(`${process.env.FRONTEND_TEST_OUTPUT}/coverage-save-receipt.json`, JSON.stringify(receipt, null, 2));
  await expect(card.getByRole('status')).toContainText('正式结论已保存');
  const after = await detail(context, 'many508');
  expect(after.conclusions).toHaveLength(before.conclusions.length + 1);
  expect(after.conclusions.at(-1).coverage.filled_order_ids).toHaveLength(508);
  await capture(page, 'coverage-real-save', { displayed: 20, submitted: 508, persisted: 508, realStaleCoverageStatus: 409 });
  const exported = await api(context, `/api/reviews/export.csv?round_id=${entry.round_id}`);
  const csv = await exported.text();
  for (const id of entry.orders) expect(csv).toContain(id);
  await Bun.write(`${process.env.FRONTEND_TEST_OUTPUT}/complete-export.csv`, csv);
  await capture(page, 'complete-export', { expectedOrderIds: 508, allPresent: true, source: '真实HTTP CSV导出' });
}
