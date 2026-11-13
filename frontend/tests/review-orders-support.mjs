import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect } from '@playwright/test';
import { z } from 'zod';
import { reviewDetailSchema } from '../src/reviewSchema.ts';

const port = z.coerce.number().int().min(1024).max(65535).refine(value => value !== 8000).parse(process.env.FRONTEND_TEST_PORT);
export const url = `http://127.0.0.1:${port}`;
export const output = resolve(z.string().min(1).parse(process.env.FRONTEND_TEST_OUTPUT));
export const password = z.string().min(1).parse(process.env.FRONTEND_TEST_PASSWORD);
const subjectSchema = z.object({ id: z.string().uuid(), round_id: z.string().uuid(), orders: z.array(z.string().uuid()), subject_type: z.enum(['plan', 'order']) });
export const fixture = z.object({ source: z.string(), subjects: z.record(z.string(), subjectSchema) }).parse(JSON.parse(await readFile(resolve(output, 'fixture.json'), 'utf8')));
export const observations = [];
export const overview = card => card.getByRole('region', { name: '关联委托概览', exact: true });
export const selected = card => card.getByRole('region', { name: '所选委托依据', exact: true });
export const rows = card => overview(card).locator('[data-review-order-id]');
export const pathFor = entry => `/api/reviews/${entry.subject_type}/${entry.id}`;
export async function api(context, path, body) {
  const response = await context.request.fetch(url + path, {
    method: body === undefined ? 'GET' : 'POST',
    ...(body === undefined ? {} : { data: body, headers: { Origin: url } }),
  });
  expect(response.ok(), `${path}: ${await response.text()}`).toBe(true);
  return response;
}
export async function detail(context, label) {
  const entry = fixture.subjects[label];
  return reviewDetailSchema.parse(await (await api(context, `${pathFor(entry)}?round_id=${entry.round_id}`)).json());
}
export async function openCard(page, label) {
  const card = page.locator(`[data-review-id="${fixture.subjects[label].id}"]`);
  await card.getByRole('button', { name: '查看计划与实际结果', exact: true }).click();
  await expect(overview(card)).toBeVisible();
  return card;
}
export async function capture(page, name, observable) {
  const artifact = resolve(output, `${name}.png`);
  await page.screenshot({ path: artifact });
  const body = resolve(output, `${name}.txt`);
  await writeFile(body, await page.locator('body').innerText());
  observations.push({ scenario: name, invocation: 'bun frontend/tests/review-orders-ui.mjs', observable, passed: true, artifact, body });
}
export async function assertSelected(card, id) {
  await expect(selected(card).locator(`[data-order-id="${id}"]`)).toBeVisible();
  await expect(overview(card).locator(`[data-review-order-id="${id}"] button`)).toHaveAttribute('aria-pressed', 'true');
}
export async function assertLayout(page, card) {
  const bounds = await rows(card).evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect();
    return { left: rect.left, right: rect.right, width: innerWidth, overflow: element.scrollWidth > element.clientWidth + 1 };
  }));
  expect(bounds.every(item => item.left >= 0 && item.right <= item.width + 1 && !item.overflow)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (bounds.length) await expect(rows(card).first().getByRole('button')).toBeVisible();
}
export async function fillDraft(card, label) {
  for (const field of ['本次结论', '与原计划的偏差', '下次怎么做']) await card.getByLabel(field, { exact: true }).fill(`${label}：${field}`);
}
export async function assertDraft(card, label) {
  for (const field of ['本次结论', '与原计划的偏差', '下次怎么做']) await expect(card.getByLabel(field, { exact: true })).toHaveValue(`${label}：${field}`);
}
export async function enterReviews(page) {
  await page.getByRole('button', { name: '复盘', exact: true }).click();
  await page.getByLabel('复盘状态', { exact: true }).selectOption('all');
}
