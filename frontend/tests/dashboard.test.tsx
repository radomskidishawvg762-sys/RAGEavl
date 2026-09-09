/**
 * Phase 3 Dashboard tests — project-scoped workspace coverage.
 * fetch is stubbed; no backend required. Verifies real-data wiring, the
 * Project→data linkage, null≠0, failure aggregation, quality-gate render,
 * insufficient-vs-sufficient trend, and the empty/error states. UI is Chinese.
 */

import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { RunListResponse } from '../src/api/types';

import { DashboardPage } from '../src/pages/DashboardPage';
import { ProjectProvider } from '../src/context/ProjectContext';
import * as fx from './fixtures';

// ECharts needs a real canvas; under jsdom it cannot render. We test whether
// the trend panel decides to render a chart or the empty state, not chart
// internals — so the wrapper is mocked to a placeholder element.
vi.mock('../src/components/charts/EChart', () => ({
  EChart: () => <div data-testid="chart" />,
}));

type Handler = (url: string) => { status?: number; body: unknown } | undefined;

function stubFetch(handler: Handler) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const result = handler(url) ?? { status: 404, body: { detail: 'nf', code: 'BIZ_NOT_FOUND', trace_id: 't' } };
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

/** Two completed runs, both with a real overall score — for a chartable trend. */
function twoScoredRuns(): RunListResponse {
  return {
    items: [
      {
        run_id: 'run-aaaa-1111', project_id: 'p1', dataset_id: 'ds-1111', config_id: 'cfg-1111',
        status: 'completed', total_records: 10, evaluated_records: 10, error_records: 0,
        evaluation_coverage: 1, overall_score: 0.82,
        created_at: '2026-08-30T01:00:00Z', started_at: '2026-08-30T01:00:01Z',
        finished_at: '2026-08-30T01:01:00Z', reproducibility_meta: {}, error_summary: null,
      },
      {
        run_id: 'run-cccc-3333', project_id: 'p1', dataset_id: 'ds-1111', config_id: 'cfg-1111',
        status: 'completed', total_records: 10, evaluated_records: 10, error_records: 0,
        evaluation_coverage: 1, overall_score: 0.74,
        created_at: '2026-08-29T01:00:00Z', started_at: '2026-08-29T01:00:01Z',
        finished_at: '2026-08-29T01:01:00Z', reproducibility_meta: {}, error_summary: null,
      },
    ] as RunListResponse['items'],
    total: 2, page: 1, page_size: 20,
  };
}

function dashboardHandler(runs: RunListResponse = fx.runList()): Handler {
  return (url) => {
    if (url.includes('/api/evaluations/run-aaaa-1111/report')) return { body: fx.report() };
    if (url.includes('/api/evaluations/run-aaaa-1111/quality-gate')) return { body: fx.qualityGate('PASS') };
    if (url.includes('/api/projects')) return { body: fx.projectList(1) };
    if (url.includes('/api/datasets')) return { body: fx.datasetList(1) };
    if (url.includes('/api/evaluations')) return { body: runs };
    return undefined;
  };
}

async function renderDash() {
  const result = render(
    <MemoryRouter>
      <ProjectProvider>
        <DashboardPage />
      </ProjectProvider>
    </MemoryRouter>,
  );
  // flush the project auto-select + data load microtasks so state settles
  await act(async () => {
    await Promise.resolve();
  });
  return result;
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('Dashboard — 数据接入', () => {
  it('从真实 API 加载项目工作区', async () => {
    stubFetch(dashboardHandler());
    await renderDash();
    // “指标概览”为唯一且仅在全量就绪后出现的元素
    await waitFor(() => expect(screen.getByText('指标概览')).toBeInTheDocument());
    expect(screen.getAllByText('project-1').length).toBeGreaterThan(0);
    expect(screen.getByText('质量概览')).toBeInTheDocument();
    expect(screen.getByText('总体质量')).toBeInTheDocument();
    expect(screen.getByText('评估覆盖率')).toBeInTheDocument();
    expect(screen.getByText('质量门禁')).toBeInTheDocument();
  });

  it('评估请求作用域绑定 active project', async () => {
    const fetchMock = stubFetch(dashboardHandler());
    await renderDash();
    await waitFor(() => expect(screen.getByText('指标概览')).toBeInTheDocument());
    const evalUrl = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/evaluations?'))?.[0];
    expect(String(evalUrl)).toContain('project_id=p1');
  });

  it('指标按 检索 / 生成 / 一致性 分组', async () => {
    stubFetch(dashboardHandler());
    await renderDash();
    await waitFor(() => expect(screen.getByText('指标概览')).toBeInTheDocument());
    expect(screen.getByText('检索')).toBeInTheDocument();
    expect(screen.getByText('生成')).toBeInTheDocument();
    expect(screen.getByText('一致性')).toBeInTheDocument();
    // 报告样例中含一个未达阈值的 consistency 指标
    expect(screen.getByTestId('metric-numerical_consistency')).toBeInTheDocument();
  });

  it('null 总体分绝不显示为 0', async () => {
    stubFetch(dashboardHandler());
    await renderDash();
    await waitFor(() => expect(screen.getByText('指标概览')).toBeInTheDocument());
    // run-bbbb 的 overall_score=null → “暂无有效分数”，而非 0
    expect(screen.getAllByTestId('no-score').length).toBeGreaterThan(0);
    // 真实的 0（numerical=0 / error_records=0）仍显示 0 —— null 与 0 不同
    expect(screen.getAllByText('0').length).toBeGreaterThan(0);
  });

  it('质量门禁 FAIL 徽标 + reason 来自 API verdict', async () => {
    const base = dashboardHandler();
    stubFetch((url) =>
      url.includes('quality-gate') ? { body: fx.qualityGate('FAIL') } : base(url),
    );
    await renderDash();
    await waitFor(() => expect(screen.getByText('指标概览')).toBeInTheDocument());
    expect(screen.getByText('未通过')).toBeInTheDocument();
    expect(screen.getByText('QUALITY_THRESHOLD_FAILED')).toBeInTheDocument();
  });
});

describe('Dashboard — 状态', () => {
  it('未选择项目时显示引导空状态', async () => {
    stubFetch((url) => (url.includes('/api/projects') ? { body: fx.projectList(0) } : undefined));
    await renderDash();
    await waitFor(() => expect(screen.getByText('请选择项目')).toBeInTheDocument());
  });

  it('项目无评估运行显示空状态', async () => {
    stubFetch(dashboardHandler({ items: [], total: 0, page: 1, page_size: 20 }));
    await renderDash();
    await waitFor(() => expect(screen.getByText(/暂无评估/)).toBeInTheDocument());
  });

  it('API 失败显示友好错误状态', async () => {
    stubFetch(() => ({
      status: 404,
      body: { detail: 'project not found', code: 'BIZ_NOT_FOUND', trace_id: 't-1' },
    }));
    await renderDash();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('未找到对应资源');
    expect(screen.getByText('技术详情')).toBeInTheDocument();
    const details = alert.querySelector('details');
    expect(details?.textContent).toContain('BIZ_NOT_FOUND');
  });
});

describe('Dashboard — 质量趋势', () => {
  it('不足两次带分运行显示“暂无足够的历史 Run”', async () => {
    stubFetch(dashboardHandler(fx.runList())); // only one run with a non-null score
    await renderDash();
    await waitFor(() => expect(screen.getByText('质量趋势')).toBeInTheDocument());
    expect(screen.getByText('暂无足够的历史 Run')).toBeInTheDocument();
  });

  it('两次带分运行渲染趋势图', async () => {
    stubFetch(dashboardHandler(twoScoredRuns()));
    await renderDash();
    await waitFor(() => expect(screen.getByTestId('chart')).toBeInTheDocument());
    expect(screen.queryByText('暂无足够的历史 Run')).toBeNull();
  });
});
