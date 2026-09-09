/**
 * T-18 pagination-contract regression tests (8 items).
 *
 * The backend limits every paginated endpoint to page_size <= 100. The
 * frontend previously hardcoded page_size=200 for /results and /diagnoses
 * (-> 422 -> the whole Workbench failed to load). The fix is "correct
 * pagination consumption", not lowering 200 to 100: the client fetches
 * page_size=100 first, inspects `total`, then keeps paging until the full
 * collection is in hand. These tests lock that behaviour in.
 *
 * The frontend never recomputes backend semantics — these tests assert only
 * (a) what `fetchAllPages` requests and concatenates, and (b) what the
 * Workbench renders. Aggregation/verdict/delta/gate/diagnosis logic stays
 * backend-only. Items 6 (no over-cap page_size in frontend source) and 8 (no
 * frontend business recomputation) are *source* invariants and live in the
 * backend suite (tests/test_pagination_contract.py), where the frontend_scan
 * module already reads the frontend/src tree.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchAllPages, MAX_PAGE_SIZE } from '../src/api/client';
import { EvaluationDetailPage } from '../src/pages/EvaluationDetailPage';
import * as fx from './fixtures';

type Handler = (url: string, init?: RequestInit) => { status?: number; body: unknown } | undefined;

function stubFetch(handler: Handler) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const result = handler(url, init) ?? { status: 404, body: { detail: 'nf', code: 'BIZ_NOT_FOUND', trace_id: 't' } };
    return {
      ok: (result.status ?? 200) < 400,
      status: result.status ?? 200,
      statusText: 'OK',
      json: async () => result.body,
    } as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function qp(url: string, key: string): string {
  return new URL(url, 'http://localhost').searchParams.get(key) ?? '';
}

async function renderDetail(runId: string) {
  const result = render(
    <MemoryRouter>
      <EvaluationDetailPage runId={runId} />
    </MemoryRouter>,
  );
  await act(async () => {
    await Promise.resolve();
  });
  return result;
}

beforeEach(() => {
  window.localStorage.clear();
});

// ---------------- 1-5: fetchAllPages is correct pagination consumption ----------------

describe('fetchAllPages (correct pagination consumption)', () => {
  it('1. 使用合法的 page_size=100 (后端上限) 发起请求', async () => {
    const fetchMock = stubFetch((url) => {
      const pageSize = Number(qp(url, 'page_size') || '0');
      expect(pageSize).toBe(MAX_PAGE_SIZE);
      const items = Array.from({ length: pageSize }, (_, i) => ({ id: String(i) }));
      return { body: { items, total: items.length, page: 1, page_size: pageSize } };
    });
    const items = await fetchAllPages<{ id: string }>((p, ps) => `/r?page=${p}&page_size=${ps}`);
    expect(items).toHaveLength(MAX_PAGE_SIZE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('2. total=250 时自动请求 3 页并最终得到 250 条', async () => {
    const requestedPages: number[] = [];
    const fetchMock = stubFetch((url) => {
      const page = Number(qp(url, 'page'));
      requestedPages.push(page);
      const pageSize = Number(qp(url, 'page_size'));
      let count = pageSize;
      if (page === 3) count = 50; // last page carries the remainder
      const items = Array.from({ length: count }, (_, i) => ({ id: `${page}-${i}` }));
      return { body: { items, total: 250, page, page_size: pageSize } };
    });
    const items = await fetchAllPages<{ id: string }>((p, ps) => `/r?page=${p}&page_size=${ps}`);
    expect(items).toHaveLength(250);
    expect(requestedPages).toEqual([1, 2, 3]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('3. total=100 时只请求 1 页', async () => {
    const fetchMock = stubFetch((url) => {
      const pageSize = Number(qp(url, 'page_size'));
      const items = Array.from({ length: pageSize }, (_, i) => ({ id: String(i) }));
      return { body: { items, total: items.length, page: 1, page_size: pageSize } };
    });
    const items = await fetchAllPages<{ id: string }>((p, ps) => `/r?page=${p}&page_size=${ps}`);
    expect(items).toHaveLength(MAX_PAGE_SIZE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('4. total=0 时不继续请求，返回空集合', async () => {
    const fetchMock = stubFetch((url) => {
      const pageSize = Number(qp(url, 'page_size'));
      return { body: { items: [], total: 0, page: 1, page_size: pageSize } };
    });
    const items = await fetchAllPages<{ id: string }>((p, ps) => `/r?page=${p}&page_size=${ps}`);
    expect(items).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('5. 第 2 页失败时整个调用 reject，不返回部分数据', async () => {
    stubFetch((url) => {
      if (qp(url, 'page') === '2') {
        return { status: 500, body: { detail: 'boom', code: 'SYS_INTERNAL', trace_id: 't' } };
      }
      const pageSize = Number(qp(url, 'page_size'));
      const items = Array.from({ length: pageSize }, (_, i) => ({ id: String(i) }));
      return { body: { items, total: 250, page: 1, page_size: pageSize } };
    });
    await expect(
      fetchAllPages<{ id: string }>((p, ps) => `/r?page=${p}&page_size=${ps}`),
    ).rejects.toThrow();
  });
});

// ---------------- 6 & 8: source invariants live in backend frontend_scan ----------------

// ---------------- 7: Workbench renders the complete collection, not the first 100 ----------------

describe('Evaluation Workbench 使用完整 results/diagnoses 不被 100 条截断', () => {
  it('7. 目标记录/诊断位于第 2 页 (>100 条) 时仍能下钻到其丰富内容', async () => {
    const page2Result = {
      id: 'res-2', record_id: 'r2', row_index: 1,
      question: '2024年营收是多少？', answer: '1200亿元', reference_answer: '1000亿元',
      is_failure: true, created_at: '2026-08-30T01:01:00Z',
    };
    const page2Diagnosis = {
      id: 'd1', result_id: 'res-2', failure_type: 'integrity.numerical_mismatch',
      related_metric: 'numerical_consistency', severity: 'CRITICAL', confidence: 'high',
      evidence_contract: 'numerical.v1', root_cause: '无法判定：数值证据不足',
      evidence: [],
      status: 'diagnosed', detail: { reason: 'ambiguous', missing_evidence: ['reference_evidence'] },
    };

    const resultsRequests: string[] = [];
    const diagnosesRequests: string[] = [];

    stubFetch((url) => {
      if (url.includes('/quality-gate')) return { body: fx.qualityGate('PASS') };
      if (/\/api\/evaluations\/run-aaaa-1111$/.test(url)) return { body: fx.runDetail() };
      if (url.includes('/report')) return { body: fx.report() };
      if (url.includes('/recommendations')) return { body: fx.recommendations() };
      // G3 result-detail endpoint must be matched BEFORE the trimmed list branch
      if (/\/results\/[\w-]+$/.test(url)) {
        return {
          body: {
            id: 'res-2', run_id: 'run-aaaa-1111', record_id: 'r2', row_index: 1,
            question: '2024年营收是多少？', answer: '1200亿元',
            contexts: ['检索上下文A', '检索上下文B'],
            reference_answer: '1000亿元',
            reference_contexts: ['2024年参考上下文'],
            is_failure: true, created_at: '2026-08-30T01:01:00Z',
            metric_results: [
              {
                name: 'numerical_consistency', category: 'integrity', score: 0, threshold: 0.9,
                passed: false, status: 'completed', metric_version: 'integrity-normalization-v1',
                comparison_basis: null, error: null,
              },
            ],
            diagnoses: [page2Diagnosis],
          },
        };
      }
      if (url.includes('/results')) {
        const page = Number(qp(url, 'page'));
        resultsRequests.push(url);
        const pageSize = Number(qp(url, 'page_size'));
        if (page === 1) {
          const items = Array.from({ length: pageSize }, (_, i) => ({
            id: `res-${i}`, record_id: `rec-${i}`, row_index: i, question: `q-${i}`,
            answer: 'a', reference_answer: 'ref', is_failure: false, created_at: '2026-08-30T01:01:00Z',
          }));
          return { body: { items, total: 150, page, page_size: pageSize } };
        }
        const items = Array.from({ length: 49 }, (_, i) => ({
          id: `res-${100 + i}`, record_id: `rec-${100 + i}`, row_index: 100 + i, question: `q-${100 + i}`,
          answer: 'a', reference_answer: 'ref', is_failure: false, created_at: '2026-08-30T01:01:00Z',
        }));
        items.push(page2Result);
        return { body: { items, total: 150, page, page_size: pageSize } };
      }
      if (url.includes('/diagnoses')) {
        const page = Number(qp(url, 'page'));
        diagnosesRequests.push(url);
        const pageSize = Number(qp(url, 'page_size'));
        if (page === 1) {
          const items = Array.from({ length: pageSize }, (_, i) => ({
            id: `dia-${i}`, result_id: `res-${i}`, failure_type: 'integrity.numerical_mismatch',
            related_metric: 'numerical_consistency', severity: 'INFO', confidence: null,
            evidence_contract: null, evidence: [], root_cause: null, status: 'diagnosed', detail: null,
          }));
          return { body: { items, total: 101, page, page_size: pageSize } };
        }
        return { body: { items: [page2Diagnosis], total: 101, page, page_size: pageSize } };
      }
      return undefined;
    });

    await renderDetail('run-aaaa-1111');
    await waitFor(() => expect(screen.getByText('失败概览')).toBeInTheDocument());
    // 完整集合必须已取得（results 到第 2 页、diagnoses 到第 2 页）
    expect(resultsRequests.some((u) => qp(u, 'page') === '2')).toBe(true);
    expect(diagnosesRequests.some((u) => qp(u, 'page') === '2')).toBe(true);

    fireEvent.click(screen.getByTestId('tab-failures'));
    const sample = await screen.findByTestId('failure-sample');
    fireEvent.click(sample);
    const drawer = await screen.findByRole('dialog');

    // Record r2 lived on results page 2 — a 100-item truncation would drop it.
    expect(drawer).toHaveTextContent('1200亿元');
    expect(drawer).toHaveTextContent('1000亿元');
    // Diagnosis d1 lived on diagnoses page 2 — truncation would fall back to failure.root_cause.
    expect(drawer).toHaveTextContent('无法判定：数值证据不足');
    expect(drawer).toHaveTextContent('检查数值抽取与单位归一化');
  });
});
