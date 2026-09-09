/**
 * T-15 frontend tests — 13 mandated points.
 * Vitest + React Testing Library; fetch is stubbed so no backend is required.
 */

import { fireEvent, render, screen, waitFor, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { DashboardPage } from '../src/pages/DashboardPage';
import { DatasetDetailPage } from '../src/pages/DatasetDetailPage';
import { EvaluationListPage } from '../src/pages/EvaluationListPage';
import { EvaluationDetailPage } from '../src/pages/EvaluationDetailPage';
import { humanizeError, ApiError } from '../src/api/client';
import { presentMetric } from '../src/utils/format';
import { ProjectProvider } from '../src/context/ProjectContext';
import * as fx from './fixtures';

type Handler = (url: string) => { status?: number; body: unknown } | undefined;

function stubFetch(handler: Handler) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const result = handler(url) ?? { status: 404, body: { detail: 'not found', code: 'BIZ_NOT_FOUND', trace_id: 't' } };
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

const detailHandler: Handler = (url) => {
  if (url.includes('/quality-gate')) return { body: fx.qualityGate('PASS') };
  if (/\/api\/evaluations\/run-aaaa-1111$/.test(url)) return { body: fx.runDetail() };
  if (url.includes('/report')) return { body: fx.report() };
  if (url.includes('/results')) return { body: fx.results() };
  if (url.includes('/diagnoses')) return { body: fx.diagnoses() };
  if (url.includes('/recommendations')) return { body: fx.recommendations() };
  if (url.includes('/progress')) return { body: fx.progress('completed') };
  return undefined;
};

beforeEach(() => {
  vi.useRealTimers();
});

// 1
describe('Dashboard', () => {
  it('1. renders project-scoped KPIs, metric groups, failures and recent runs', async () => {
    stubFetch((url) => {
      if (url.includes('/api/evaluations/run-aaaa-1111/report')) return { body: fx.report() };
      if (url.includes('/api/evaluations/run-aaaa-1111/quality-gate')) return { body: fx.qualityGate('PASS') };
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      if (url.includes('/api/datasets')) return { body: fx.datasetList(1) };
      if (url.includes('/api/evaluations')) return { body: fx.runList() };
      return undefined;
    });
    render(
      <MemoryRouter>
        <ProjectProvider>
          <DashboardPage />
        </ProjectProvider>
      </MemoryRouter>,
    );
    await act(async () => {
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByText('指标概览')).toBeInTheDocument());
    // project-scoped header from the active project, not a projects-count card
    expect(screen.getAllByText('project-1').length).toBeGreaterThan(0);
    expect(screen.getByText('质量概览')).toBeInTheDocument();
    expect(screen.getByText('评估覆盖率')).toBeInTheDocument();
    expect(screen.getByText('总体质量')).toBeInTheDocument();
    expect(screen.getByText('质量门禁')).toBeInTheDocument();
    expect(screen.getByText('指标概览')).toBeInTheDocument();
    expect(screen.getByText('检索')).toBeInTheDocument();
    expect(screen.getByText('生成')).toBeInTheDocument();
    expect(screen.getByText('一致性')).toBeInTheDocument();
    expect(screen.getByText('失败概览')).toBeInTheDocument();
    expect(screen.getAllByTestId('failure-row').length).toBeGreaterThan(0);
    expect(screen.getAllByText('最近运行').length).toBeGreaterThan(0);
    // null overall_score must not render as 0
    expect(screen.getAllByTestId('no-score').length).toBeGreaterThan(0);
    // quality gate PASS badge (中文化)
    expect(screen.getAllByText('通过').length).toBeGreaterThan(0);
  });
});

// 2 / 11
describe('Dataset detail page', () => {
  function datasetDetailHandler(locked: boolean) {
    return (url: string) => {
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      if (url.includes('/validation')) return { body: fx.datasetValidationEndpoint(true) };
      if (url.includes('/records')) return { body: fx.datasetRecords() };
      return { body: fx.dataset(locked) };
    };
  }

  it('2. renders dataset detail metadata', async () => {
    stubFetch(datasetDetailHandler(false));
    render(
      <MemoryRouter>
        <ProjectProvider>
          <DatasetDetailPage datasetId="ds-1111" />
        </ProjectProvider>
      </MemoryRouter>,
    );
    // Overview Tab（默认）：元数据卡片
    await waitFor(() => expect(screen.getByTestId('tab-overview')).toBeInTheDocument());
    expect(screen.getAllByText('300').length).toBeGreaterThan(0);
    expect(screen.getAllByText('有效').length).toBeGreaterThan(0);
    expect(screen.getAllByText('未锁定').length).toBeGreaterThan(0);
    // Validation Tab：5 类校验报告
    fireEvent.click(screen.getByTestId('tab-validation'));
    expect(screen.getByTestId('validation-summary')).toBeInTheDocument();
    // Records Tab：记录 DataTable
    fireEvent.click(screen.getByTestId('tab-records'));
    expect(screen.getAllByTestId('record-row').length).toBeGreaterThan(0);
  });

  it('11. locked dataset shows locked state and exposes no edit entry', async () => {
    stubFetch(datasetDetailHandler(true));
    render(
      <MemoryRouter>
        <ProjectProvider>
          <DatasetDetailPage datasetId="ds-1111" />
        </ProjectProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('已锁定')).toBeInTheDocument());
    expect(screen.getByText(/已锁定 · 不可编辑/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /编辑|导入/ })).toBeNull();
  });
});

// 3
describe('Evaluation list', () => {
  it('3. renders rows, gate column, filters and pagination controls', async () => {
    stubFetch((url) => {
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      if (url.includes('/api/datasets')) return { body: fx.datasetList(1) };
      if (url.includes('/quality-gate')) return { body: fx.qualityGate('PASS') };
      if (url.includes('/api/evaluations')) return { body: fx.runList(2) };
      return undefined;
    });
    render(
      <MemoryRouter>
        <ProjectProvider>
          <EvaluationListPage />
        </ProjectProvider>
      </MemoryRouter>,
    );
    await act(async () => {
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getAllByTestId('run-row').length).toBe(2));
    expect(screen.getByText('新建评估')).toBeInTheDocument();
    expect(screen.getAllByText('通过').length).toBeGreaterThan(0); // quality gate column (中文)
    expect(screen.getAllByText(/共 2 次/).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
  });
});

// 4-10, 12-13
describe('Evaluation detail', () => {
  it('4. renders the workspace sections across tabs', async () => {
    stubFetch(detailHandler);
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="run-aaaa-1111" />
      </MemoryRouter>,
    );
    // Overview（默认）：运行信息 + 质量概览 + 失败概览 + 执行概览
    await waitFor(() => expect(screen.getByText('质量概览')).toBeInTheDocument());
    expect(screen.getByText('运行信息')).toBeInTheDocument();
    expect(screen.getByText('失败概览')).toBeInTheDocument();
    expect(screen.getByText('执行概览')).toBeInTheDocument();
    // Metrics Tab
    fireEvent.click(screen.getByTestId('tab-metrics'));
    expect(screen.getByText('指标概览')).toBeInTheDocument();
    expect(screen.getByTestId('metric-numerical_consistency')).toBeInTheDocument();
    // Failures Tab
    fireEvent.click(screen.getByTestId('tab-failures'));
    expect(screen.getByText('失败样本 Failure Samples')).toBeInTheDocument();
    // Diagnostics Tab
    fireEvent.click(screen.getByTestId('tab-diagnostics'));
    expect(screen.getByText('无法判定 Undetermined')).toBeInTheDocument();
    // Configuration Tab
    fireEvent.click(screen.getByTestId('tab-configuration'));
    expect(screen.getByText('Run 快照 Configuration')).toBeInTheDocument();
  });

  it('5. polling stops once the run reaches a terminal status', async () => {
    let calls = 0;
    stubFetch((url) => {
      if (url.includes('/progress')) {
        calls += 1;
        return { body: fx.progress('completed') };
      }
      return detailHandler(url);
    });
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="run-aaaa-1111" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.queryByText(/评估进行中/)).toBeNull());
    // report status already terminal -> no further polling after it settles
    await new Promise((r) => setTimeout(r, 50));
    const afterSettle = calls;
    await new Promise((r) => setTimeout(r, 60));
    expect(calls).toBe(afterSettle); // polling stopped
  });

  it('6. score=null never renders as 0', async () => {
    stubFetch(detailHandler);
    render(
      <MemoryRouter>
        <ProjectProvider>
          <EvaluationDetailPage runId="run-aaaa-1111" />
        </ProjectProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('质量概览')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('tab-metrics'));
    await waitFor(() => expect(screen.getByTestId('metric-faithfulness')).toBeInTheDocument());
    const row = screen.getByTestId('metric-faithfulness');
    expect(row).toHaveTextContent('暂无有效分数');
    expect(row).not.toHaveTextContent('0.0');
  });

  it('7. not_configured shows the Not Configured state', async () => {
    stubFetch(detailHandler);
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="run-aaaa-1111" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('质量概览')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('tab-metrics'));
    await waitFor(() => expect(screen.getByTestId('metric-faithfulness')).toBeInTheDocument());
    expect(screen.getByTestId('metric-faithfulness')).toHaveTextContent('未配置');
  });

  it('8. undetermined shows reason and missing evidence', async () => {
    stubFetch(detailHandler);
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="run-aaaa-1111" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('质量概览')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('tab-diagnostics'));
    const item = await screen.findByTestId('undetermined-item');
    expect(item).toHaveTextContent('comparison_type=ambiguous');
    expect(item).toHaveTextContent('reference_evidence');
  });

  it('9. execution error shows code and sanitized message', async () => {
    stubFetch(detailHandler);
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="run-aaaa-1111" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('质量概览')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('tab-diagnostics'));
    const item = await screen.findByTestId('error-item');
    expect(item).toHaveTextContent('EXT_RAG_ADAPTER_TIMEOUT');
    expect(item).toHaveTextContent('RAG adapter request failed: timeout');
  });

  it('10. failure sample shows severity / type / metric', async () => {
    stubFetch(detailHandler);
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="run-aaaa-1111" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('质量概览')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('tab-failures'));
    const item = await screen.findByTestId('failure-sample');
    expect(item).toHaveTextContent('integrity.numerical_mismatch');
    expect(item).toHaveTextContent('严重'); // CRITICAL → 严重
    expect(item).toHaveTextContent('分析 →');
  });

  it('12. secrets are never rendered', async () => {
    stubFetch(detailHandler);
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="run-aaaa-1111" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('质量概览')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('tab-configuration'));
    await waitFor(() => expect(screen.getByText('Run 快照 Configuration')).toBeInTheDocument());
    expect(screen.queryByText(/sk-SHOULD-NEVER-APPEAR/)).toBeNull();
    expect(screen.getByText('judge_model')).toBeInTheDocument();
    expect(screen.getByText('gpt-4o-mini')).toBeInTheDocument();
  });

  it('13. API errors render a friendly state with collapsed technical details', async () => {
    stubFetch(() => ({
      status: 404,
      body: { detail: 'run x not found', code: 'BIZ_NOT_FOUND', trace_id: 't-1' },
    }));
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="missing" />
      </MemoryRouter>,
    );
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('未找到对应资源');
    expect(screen.getByText('技术详情')).toBeInTheDocument();
    const details = alert.querySelector('details');
    expect(details?.textContent).toContain('BIZ_NOT_FOUND');
    expect(details?.textContent).toContain('t-1');
  });
});

describe('presentation rules', () => {
  it('completed + passed=false is a quality failure, not an execution error', () => {
    expect(presentMetric('completed', false).presentation).toBe('quality_failure');
    expect(presentMetric('completed', true).presentation).toBe('pass');
    expect(presentMetric('not_configured', null).presentation).toBe('not_configured');
    expect(presentMetric('undetermined', null).presentation).toBe('undetermined');
    expect(presentMetric('error', null).presentation).toBe('error');
  });

  it('error codes map to human-readable text', () => {
    expect(humanizeError(new ApiError(409, { detail: 'x', code: 'BIZ_DATASET_LOCKED', trace_id: 't' }))).toContain('锁定');
    expect(humanizeError(new ApiError(503, { detail: 'x', code: 'EXT_JUDGE_UNAVAILABLE', trace_id: 't' }))).toContain('Judge');
    expect(humanizeError(new ApiError(500, { detail: 'boom', code: 'SYS_INTERNAL', trace_id: 't' }))).toContain('内部错误');
    expect(humanizeError(new Error('network'))).toContain('网络');
  });
});
