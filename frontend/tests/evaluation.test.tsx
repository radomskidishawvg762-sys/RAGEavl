/**
 * Phase 5 Evaluation tests — 列表 / 工作台下钻 / 新建向导（真实 gap 状态）。
 * fetch stub；前端不重算、不 mock 后端语义。
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { EvaluationListPage } from '../src/pages/EvaluationListPage';
import { EvaluationDetailPage } from '../src/pages/EvaluationDetailPage';
import { NewEvaluationPage } from '../src/pages/NewEvaluationPage';
import { ProjectProvider } from '../src/context/ProjectContext';
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

beforeEach(() => {
  window.localStorage.clear();
});

async function renderWithProject(ui: React.ReactNode) {
  const result = render(
    <MemoryRouter>
      <ProjectProvider>{ui}</ProjectProvider>
    </MemoryRouter>,
  );
  await act(async () => {
    await Promise.resolve();
  });
  return result;
}

// ---------------- 评估运行列表 ----------------

describe('评估运行列表', () => {
  it('展示运行，质量门禁列中文化，支持状态筛选', async () => {
    stubFetch((url) => {
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      if (url.includes('/api/datasets')) return { body: fx.datasetList(1) };
      if (url.includes('/quality-gate')) return { body: fx.qualityGate('PASS') };
      if (url.includes('/api/evaluations')) return { body: fx.runList(2) };
      return undefined;
    });
    await renderWithProject(<EvaluationListPage />);
    await waitFor(() => expect(screen.getAllByTestId('run-row').length).toBe(2));
    // 门禁列（通过 / FAIL 中文化）
    expect(screen.getAllByText('通过').length).toBeGreaterThan(0);
    // 新建评估入口
    expect(screen.getByText('新建评估')).toBeInTheDocument();
    // 状态筛选下拉
    const statusSelect = screen.getByLabelText(/状态/);
    expect(statusSelect).toBeInTheDocument();
  });
});

// ---------------- 评估工作台（失败下钻） ----------------

describe('评估工作台', () => {
  const page2Diagnosis = {
    id: 'd1', result_id: 'res-2', failure_type: 'integrity.numerical_mismatch',
    related_metric: 'numerical_consistency', severity: 'CRITICAL', confidence: 'high',
    evidence_contract: 'integrity.numerical_mismatch.v1',
    evidence: [
      {
        type: 'reference_evidence', source: 'reference_contexts',
        locator: 'record.reference_contexts[0]', content: '参考 1000亿元', metadata: null,
      },
    ],
    root_cause: '生成阶段数值与参考上下文不一致',
    status: 'diagnosed', detail: null,
  };

  function detailHandler(url: string): { status?: number; body: unknown } | undefined {
    if (url.includes('/quality-gate')) return { body: fx.qualityGate('PASS') };
    if (/\/api\/evaluations\/run-aaaa-1111$/.test(url)) return { body: fx.runDetail() };
    if (url.includes('/report')) return { body: fx.report() };
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
      return {
        body: {
          items: [
            { id: 'res-2', record_id: 'r2', row_index: 1, question: '2024年营收是多少？', answer: '1200亿元', reference_answer: '1000亿元', is_failure: true, created_at: '2026-08-30T01:01:00Z' },
          ],
          total: 1, page: 1, page_size: 200,
        },
      };
    }
    if (url.includes('/diagnoses')) return { body: { items: [page2Diagnosis], total: 1, page: 1, page_size: 200 } };
    if (url.includes('/recommendations')) return { body: fx.recommendations() };
    return undefined;
  }

  it('失败样本点击 → 分析抽屉展示诊断与建议', async () => {
    stubFetch(detailHandler);
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="run-aaaa-1111" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('失败概览')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('tab-failures'));
    const sample = await screen.findByTestId('failure-sample');
    fireEvent.click(sample);
    const drawer = await screen.findByRole('dialog');
    expect(drawer).toHaveTextContent('样本 Sample');
    expect(drawer).toHaveTextContent('Reference Answer');
    expect(drawer).toHaveTextContent('1000亿元');
    expect(drawer).toHaveTextContent('修复建议 Recommendation');
    expect(drawer).toHaveTextContent('检查数值抽取与单位归一化');
    // G2: evidence items are echoed from the persisted JSONB
    expect(screen.getByTestId('evidence-reference_evidence')).toBeInTheDocument();
    expect(drawer).toHaveTextContent('生成阶段数值与参考上下文不一致');
  });

  it('质量概览 / 指标概览 / 失败概览 / 失败样本均呈现（分 Tab）', async () => {
    stubFetch(detailHandler);
    render(
      <MemoryRouter>
        <EvaluationDetailPage runId="run-aaaa-1111" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('质量概览')).toBeInTheDocument());
    expect(screen.getByText('失败概览')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('tab-metrics'));
    expect(screen.getByText('指标概览')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('tab-failures'));
    expect(screen.getByText('失败样本 Failure Samples')).toBeInTheDocument();
    // null 分数不显示为 0
    fireEvent.click(screen.getByTestId('tab-metrics'));
    expect(screen.getAllByTestId('no-score').length).toBeGreaterThan(0);
  });
});

// ---------------- 新建评估向导 ----------------

describe('新建评估向导', () => {
  function wizardHandler(url: string, init?: RequestInit): { status?: number; body: unknown } | undefined {
    // config catalog (read) — never touches the DB
    if (url.includes('/api/configs/profiles')) return { body: fx.profileList() };
    if (url.includes('/api/metrics')) return { body: fx.metricList() };
    // save config (POST) -> config_id
    if (url.endsWith('/configs')) return { body: fx.saveConfigResult() };
    // launch run (POST) -> run_id
    if (url === '/api/evaluations' && init?.method === 'POST') return { body: fx.createRunResult() };
    return undefined;
  }

  function renderWizardWithRoutes() {
    return render(
      <MemoryRouter initialEntries={['/evaluations/new']}>
        <ProjectProvider>
          <Routes>
            <Route path="/evaluations/new" element={<NewEvaluationPage />} />
            <Route path="/evaluations/:runId" element={<div data-testid="workbench">评估工作台</div>} />
          </Routes>
        </ProjectProvider>
      </MemoryRouter>,
    );
  }

  it('5 步走完后「开始评估」真实发起 Run 并跳转工作台', async () => {
    const fetchMock = stubFetch((url, init) => {
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      if (url.includes('/api/datasets')) return { body: fx.datasetList(1) };
      return wizardHandler(url, init);
    });
    renderWizardWithRoutes();
    await act(async () => { await Promise.resolve(); });

    // Step 1 数据集
    const ds = await screen.findByTestId('select-dataset');
    fireEvent.click(ds);
    fireEvent.click(screen.getByText('下一步'));

    // Step 2 Profile 列表（真实目录）
    const profile = await screen.findByTestId('select-profile');
    expect(profile).toHaveTextContent('default');
    fireEvent.click(profile);
    fireEvent.click(screen.getByText('下一步'));

    // Step 3 指标（Run Configuration 编辑器：Profile Default / Run Override / Effective）
    expect(screen.getByTestId('metric-editor')).toBeInTheDocument();
    expect(screen.getByText('numerical_consistency')).toBeInTheDocument();
    // Profile Default 列显示 Profile 值；Run Override 输入初始为空（留空=保留 Profile）
    expect(screen.getByTestId('metric-effective-numerical_consistency')).toHaveTextContent('0.9');
    expect(screen.getByTestId('metric-threshold-numerical_consistency')).toHaveValue(null);
    fireEvent.click(screen.getByText('下一步'));

    // Step 4 RAG 输入（未配置黄金元数据）
    expect(screen.getByTestId('rag-not-configured')).toBeInTheDocument();
    fireEvent.click(screen.getByText('下一步'));

    // Step 5 确认并启动 → 「开始评估」可用
    const start = await screen.findByTestId('start-evaluation');
    expect(start).not.toBeDisabled();
    fireEvent.click(start);

    // save config 已调用（POST /api/projects/p1/configs）
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/projects/p1/configs',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    // 真正发起 Evaluation Run（POST /api/evaluations）
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/evaluations',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    // 跳转评估工作台
    expect(await screen.findByTestId('workbench')).toBeInTheDocument();
  });

  it('已锁定数据集在 Step5 阻止启动并提示', async () => {
    stubFetch((url) => {
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      if (url.includes('/api/datasets')) {
        return { body: { items: [fx.dataset(true)], total: 1, page: 1, page_size: 20 } };
      }
      return wizardHandler(url);
    });
    renderWizardWithRoutes();
    await act(async () => { await Promise.resolve(); });

    const ds = await screen.findByTestId('select-dataset');
    fireEvent.click(ds);
    fireEvent.click(screen.getByText('下一步'));
    const profile = await screen.findByTestId('select-profile');
    fireEvent.click(profile);
    fireEvent.click(screen.getByText('下一步'));
    fireEvent.click(screen.getByText('下一步'));
    fireEvent.click(screen.getByText('下一步'));
    await waitFor(() =>
      expect(screen.getByTestId('locked-hint')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('start-evaluation')).toBeDisabled();
  });

  it('修改阈值 → metric_overrides 进入 POST 请求体（G4 Run Configuration）', async () => {
    const fetchMock = stubFetch((url, init) => {
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      if (url.includes('/api/datasets')) return { body: fx.datasetList(1) };
      return wizardHandler(url, init);
    });
    renderWizardWithRoutes();
    await act(async () => { await Promise.resolve(); });

    const ds = await screen.findByTestId('select-dataset');
    fireEvent.click(ds);
    fireEvent.click(screen.getByText('下一步'));
    const profile = await screen.findByTestId('select-profile');
    fireEvent.click(profile);
    fireEvent.click(screen.getByText('下一步'));

    // Step 3: 把 numerical_consistency 阈值从 Profile 的 0.9 改为 0.5
    const th = screen.getByTestId('metric-threshold-numerical_consistency');
    fireEvent.change(th, { target: { value: '0.5' } });
    // 覆盖计数进入按钮文案（下一步（1 项覆盖））
    expect(screen.getByText(/1 项覆盖/)).toBeInTheDocument();
    // 未修改的指标不产生覆盖
    fireEvent.click(screen.getByText(/下一步/)); // → Step 4
    fireEvent.click(screen.getByText(/下一步/)); // → Step 5
    const start = await screen.findByTestId('start-evaluation');
    expect(start).not.toBeDisabled();
    fireEvent.click(start);

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([u, init]) => u === '/api/evaluations' && (init as RequestInit)?.method === 'POST',
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call![1] as RequestInit).body));
      expect(body.metric_overrides).toEqual({ numerical_consistency: { threshold: 0.5 } });
    });
    expect(await screen.findByTestId('workbench')).toBeInTheDocument();
  });
});
