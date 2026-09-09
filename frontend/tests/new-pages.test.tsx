/**
 * Phase 2 产品闭环 — 新页面测试（Projects / Compare / Regression / Quality Gate /
 * Failure Explorer / Profiles / Metrics / Settings + Drawer evidence）。
 * 全部走 fetch stub（与既有测试同一模式）；断言「渲染后端返回的数据」，
 * 不重算任何业务语义。null 分数永不显示为 0。
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { ProjectsPage } from '../src/pages/ProjectsPage';
import { ComparePage } from '../src/pages/ComparePage';
import { RegressionPage } from '../src/pages/RegressionPage';
import { QualityGatePage } from '../src/pages/QualityGatePage';
import { FailureExplorerPage } from '../src/pages/FailureExplorerPage';
import { ProfilesPage } from '../src/pages/ProfilesPage';
import { MetricsPage } from '../src/pages/MetricsPage';
import { SettingsPage } from '../src/pages/SettingsPage';
import { EvaluationDetailPage } from '../src/pages/EvaluationDetailPage';
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

function renderWithProject(ui: React.ReactNode) {
  return render(
    <MemoryRouter>
      <ProjectProvider>{ui}</ProjectProvider>
    </MemoryRouter>,
  );
}

async function settled() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

// ---------------- ProjectsPage ----------------

describe('ProjectsPage (Project Workspace)', () => {
  it('创建项目走真实 POST /api/projects 并刷新列表', async () => {
    // 有状态 stub：POST 成功后列表包含新项目（模拟后端持久化 + 重查）
    const projects = [...fx.projectList(1).items];
    const fetchMock = stubFetch((url, init) => {
      if (url === '/api/projects' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        const created = {
          id: 'p-new',
          name: body.name,
          domain: body.domain ?? 'general',
          status: 'active',
          created_at: '2026-08-31T10:00:00Z',
        };
        projects.push(created);
        return { status: 201, body: created };
      }
      if (url.includes('/api/projects')) {
        return { body: { items: projects, total: projects.length, page: 1, page_size: 20 } };
      }
      return undefined;
    });
    renderWithProject(<ProjectsPage />);
    await settled();

    fireEvent.change(screen.getByTestId('project-name-input'), { target: { value: 'finance-eval' } });
    fireEvent.click(screen.getByTestId('project-create-submit'));
    await settled();

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/projects',
      expect.objectContaining({ method: 'POST' }),
    );
    await waitFor(() => expect(screen.getByText('finance-eval')).toBeInTheDocument());
  });

  it('重复名称显示后端错误（不伪造成功）', async () => {
    stubFetch((url, init) => {
      if (url === '/api/projects' && init?.method === 'POST') {
        return {
          status: 409,
          body: { detail: "project name 'dup' already exists", code: 'BIZ_PROJECT_NAME_EXISTS', trace_id: 't' },
        };
      }
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    });
    renderWithProject(<ProjectsPage />);
    await settled();

    fireEvent.change(screen.getByTestId('project-name-input'), { target: { value: 'dup' } });
    fireEvent.click(screen.getByTestId('project-create-submit'));
    await settled();

    // humanizeError 将已知错误码转为中文（原始 code 不直接暴露给用户）
    const err = await screen.findByTestId('project-create-error');
    expect(err).toHaveTextContent('项目名称已存在');
  });

  it('查看摘要渲染 dataset/run 计数 + latest run + gate（G5 数据）', async () => {
    stubFetch((url) => {
      if (url === '/api/projects/p1') return { body: fx.projectSummary() };
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    });
    renderWithProject(<ProjectsPage />);
    await settled();

    fireEvent.click(screen.getByTestId('summary-project-1'));
    await waitFor(() => expect(screen.getByTestId('project-summary')).toBeInTheDocument());
    expect(screen.getByTestId('project-latest-run')).toBeInTheDocument();
    // gate badge from summary (PASS -> 通过)
    expect(screen.getAllByText('通过').length).toBeGreaterThan(0);
    // counts rendered as-is from backend
    expect(screen.getByText('3')).toBeInTheDocument();
  });
});

// ---------------- ComparePage ----------------

describe('ComparePage', () => {
  function compareHandler(status: 'DIRECT' | 'LIMITED' | 'BLOCKED'): Handler {
    return (url) => {
      if (url.includes('/api/comparisons?')) return { body: fx.comparison(status) };
      if (url.includes('/api/evaluations')) return { body: fx.runList(2) };
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    };
  }

  it('DIRECT：渲染 delta / relative_delta（原样透传后端数值）', async () => {
    stubFetch(compareHandler('DIRECT'));
    renderWithProject(<ComparePage />);
    await settled();

    fireEvent.change(screen.getByTestId('baseline-select'), { target: { value: 'run-aaaa-1111' } });
    fireEvent.change(screen.getByTestId('candidate-select'), { target: { value: 'run-bbbb-2222' } });
    fireEvent.click(screen.getByTestId('compare-submit'));

    expect(await screen.findByTestId('comparability-banner')).toBeInTheDocument();
    expect(screen.getByTestId('cmp-temporal_consistency')).toHaveTextContent('0.1');
    expect(screen.getByTestId('cmp-temporal_consistency')).toHaveTextContent('12.5%');
    expect(screen.getByTestId('cmp-numerical_consistency')).toHaveTextContent('-0.1');
    // overall delta from backend
    expect(screen.getByTestId('overall-comparison')).toHaveTextContent('-0.03');
  });

  it('BLOCKED：metrics 为空 + 只显示后端原因（不自行判断）', async () => {
    stubFetch(compareHandler('BLOCKED'));
    renderWithProject(<ComparePage />);
    await settled();

    fireEvent.change(screen.getByTestId('baseline-select'), { target: { value: 'run-aaaa-1111' } });
    fireEvent.change(screen.getByTestId('candidate-select'), { target: { value: 'run-bbbb-2222' } });
    fireEvent.click(screen.getByTestId('compare-submit'));

    expect(await screen.findByTestId('blocked-note')).toBeInTheDocument();
    expect(screen.getByTestId('comparability-reasons')).toHaveTextContent('enabled metrics differ');
    expect(screen.queryByTestId('comparison-table')).not.toBeInTheDocument();
  });
});

// ---------------- RegressionPage ----------------

describe('RegressionPage', () => {
  it('渲染 overall verdict / category counts / epsilon source / trade-off', async () => {
    stubFetch((url) => {
      if (url.includes('/api/comparisons/regression')) return { body: fx.regression() };
      if (url.includes('/api/evaluations')) return { body: fx.runList(2) };
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    });
    renderWithProject(<RegressionPage />);
    await settled();

    fireEvent.change(screen.getByTestId('regression-baseline-select'), { target: { value: 'run-aaaa-1111' } });
    fireEvent.change(screen.getByTestId('regression-candidate-select'), { target: { value: 'run-bbbb-2222' } });
    fireEvent.click(screen.getByTestId('regression-submit'));

    expect(await screen.findByTestId('trade-off-banner')).toBeInTheDocument();
    expect(screen.getByTestId('regression-overall')).toHaveTextContent('混合变化');
    expect(screen.getByTestId('regression-category-integrity')).toHaveTextContent('改进 1');
    expect(screen.getByTestId('regression-category-integrity')).toHaveTextContent('回归 1');
    expect(screen.getByTestId('reg-temporal_consistency')).toHaveTextContent('改进');
    expect(screen.getByTestId('reg-numerical_consistency')).toHaveTextContent('回归');
    // epsilon + source rendered (traceability)
    expect(screen.getByTestId('reg-temporal_consistency')).toHaveTextContent('配置');
  });
});

// ---------------- QualityGatePage ----------------

describe('QualityGatePage', () => {
  it('FAIL：区分 QUALITY_THRESHOLD_FAILED 与指标级 FAIL，null 不显示为 0', async () => {
    stubFetch((url) => {
      if (url.includes('/quality-gate')) return { body: fx.qualityGate('FAIL') };
      if (url.includes('/api/evaluations')) return { body: fx.runList(2) };
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    });
    renderWithProject(<QualityGatePage />);
    await settled();

    fireEvent.change(screen.getByTestId('gate-run-select'), { target: { value: 'run-aaaa-1111' } });
    fireEvent.click(screen.getByTestId('gate-submit'));

    expect(await screen.findByTestId('gate-status')).toBeInTheDocument();
    expect(screen.getByTestId('gate-reasons')).toHaveTextContent('QUALITY_THRESHOLD_FAILED');
    // the reason explanation distinguishes threshold-fail from not-evaluable
    expect(screen.getByTestId('gate-reasons')).toHaveTextContent('低于配置阈值');
    expect(screen.getByTestId('gate-numerical_consistency')).toBeInTheDocument();
    expect(screen.getByTestId('gate-entity_consistency')).toHaveTextContent('未通过');
  });
});

// ---------------- FailureExplorerPage ----------------

describe('FailureExplorerPage', () => {
  it('列出失败诊断并打开抽屉：Sample I/O + comparison_basis + Evidence items', async () => {
    stubFetch((url) => {
      if (url.includes('/diagnoses')) return { body: fx.diagnosesWithEvidence() };
      if (url.includes('/results?')) {
        return {
          body: {
            items: [
              {
                id: 'res-2', record_id: 'r2', row_index: 1,
                question: '2024年营收是多少？', answer: '1200亿元',
                reference_answer: '1000亿元', is_failure: true,
                created_at: '2026-08-30T01:01:00Z',
              },
            ],
            total: 1, page: 1, page_size: 100,
          },
        };
      }
      if (url.includes('/recommendations')) return { body: fx.recommendations() };
      if (url.includes('/results/res-2')) return { body: fx.resultDetail() };
      if (url.includes('/api/evaluations')) return { body: fx.runList(2) };
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    });
    renderWithProject(<FailureExplorerPage />);
    await settled();

    fireEvent.change(screen.getByTestId('failure-run-select'), { target: { value: 'run-aaaa-1111' } });
    await settled();

    const row = await screen.findByTestId('failure-row');
    expect(row).toHaveTextContent('integrity.numerical_mismatch');
    expect(row).toHaveTextContent('2024年营收是多少？');

    fireEvent.click(screen.getByTestId('open-diagnosis'));
    const drawer = await screen.findByTestId('diagnosis-drawer');
    // G3: full sample I/O from result detail
    expect(drawer).toHaveTextContent('检索上下文 A');
    expect(drawer).toHaveTextContent('2024年度公司营业收入为1000亿元');
    // G2: evidence items rendered with type + content
    expect(screen.getByTestId('evidence-reference_evidence')).toBeInTheDocument();
    expect(screen.getByTestId('evidence-contract')).toHaveTextContent('integrity.numerical_mismatch.v1');
    // comparison_basis visible
    expect(drawer).toHaveTextContent('comparison_basis');
    // recommendation
    expect(screen.getAllByTestId('recommendation-item').length).toBeGreaterThan(0);
  });
});

// ---------------- ProfilesPage（Configuration 工作区，Phase C/D） ----------------

describe('ProfilesPage (Configuration workspace)', () => {
  const configDetail = {
    config_id: 'c1',
    name: 'strict_financial:v1',
    source: 'imported',
    created_at: '2026-01-01T00:00:00Z',
    profile: 'strict_financial',
    version: 'v1',
    domain: 'general',
    config_version: 'a'.repeat(64),
    metrics: [
      { name: 'entity_consistency', enabled: true, threshold: 0.9, weight: 1.0, direction: 'higher_is_better' },
    ],
    severity_mapping: { numerical_mismatch: 'CRITICAL' },
    quality_gate: null,
    pipeline: null,
    judge: { provider: 'openai', model: 'gpt', model_version: 'v1', temperature: 0, max_tokens: 1024, timeout: 30, retry: 3 },
    rag_input: { mode: 'golden_replay', url: null, timeout: null, retry: null },
    yaml: 'profile:\n  name: strict_financial\n',
  };

  it('工程配置工作区：三页签 + 配置版本表（PUBLISHED）+ View 抽屉，无假保存按钮', async () => {
    stubFetch((url) => {
      if (url.includes('/api/projects/p1/configs')) {
        return {
          body: {
            items: [
              {
                config_id: 'c1', name: 'strict_financial:v1', domain: 'general',
                profile: 'strict_financial', version: 'v1', source: 'imported',
                config_version: 'a'.repeat(64), created_at: '2026-01-01T00:00:00Z',
              },
            ],
            total: 1,
          },
        };
      }
      if (url.includes('/api/configs/c1')) return { body: configDetail };
      if (url.includes('/api/configs/profiles')) return { body: fx.profileList(1) };
      if (url.includes('/api/metrics')) return { body: fx.metricList() };
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    });
    renderWithProject(<ProfilesPage />);
    await settled();

    // 三个页签（Table/Tabs 工程工作台，非 AI 卡片）
    expect(screen.getByTestId('tab-profiles')).toBeInTheDocument();
    expect(screen.getByTestId('tab-metrics')).toBeInTheDocument();
    expect(screen.getByTestId('tab-import')).toBeInTheDocument();

    // 配置版本表：stored imported 行显示 PUBLISHED + 完整操作集
    expect(screen.getByTestId('config-row-strict_financial:v1')).toBeInTheDocument();
    expect(screen.getByText('PUBLISHED')).toBeInTheDocument();
    expect(screen.getByTestId('config-view-c1')).toBeInTheDocument();
    expect(screen.getByTestId('config-duplicate-c1')).toBeInTheDocument();
    expect(screen.getByTestId('config-import-version-c1')).toBeInTheDocument();
    expect(screen.getByTestId('config-export-c1')).toBeInTheDocument();
    // 无假保存 —— 旧版本不可变，唯一变更路径是 Duplicate / Import New Version
    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument();

    // View 打开详情抽屉（切到 Raw YAML 页签，只读 + 复制/导出）
    fireEvent.click(screen.getByTestId('config-view-c1'));
    await screen.findByTestId('config-detail-drawer');
    fireEvent.click(screen.getByRole('tab', { name: 'Raw YAML' }));
    expect(await screen.findByTestId('detail-yaml')).toBeInTheDocument();
    expect(screen.getByTestId('yaml-copy')).toBeInTheDocument();
    expect(screen.getByTestId('yaml-export')).toBeInTheDocument();
  });
});

// ---------------- MetricsPage ----------------

describe('MetricsPage', () => {
  it('按分类分组展示 + 搜索过滤', async () => {
    stubFetch((url) => {
      if (url.includes('/api/configs/profiles')) return { body: fx.profileList(1) };
      if (url.includes('/api/metrics')) return { body: fx.metricList() };
      return undefined;
    });
    renderWithProject(<MetricsPage />);
    await settled();

    expect(screen.getByTestId('metric-card-context_recall')).toBeInTheDocument();
    expect(screen.getByTestId('metric-card-numerical_consistency')).toBeInTheDocument();
    expect(screen.getByText('RETRIEVAL · 检索（2）')).toBeInTheDocument();
    expect(screen.getByText('GENERATION · 生成（2）')).toBeInTheDocument();
    expect(screen.getByText('INTEGRITY · 一致性（3）')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('metrics-search'), { target: { value: 'numerical' } });
    expect(screen.getByTestId('metric-card-numerical_consistency')).toBeInTheDocument();
    expect(screen.queryByTestId('metric-card-context_recall')).not.toBeInTheDocument();
  });
});

// ---------------- SettingsPage ----------------

describe('SettingsPage', () => {
  it('显示真实 health 状态 + 环境变量管理声明（无假表单）', async () => {
    stubFetch((url) => {
      if (url === '/api/health') {
        return { body: { app: 'healthy', database: 'healthy', detail: { db_latency_ms: 12 } } };
      }
      return undefined;
    });
    renderWithProject(<SettingsPage />);
    await settled();

    expect(screen.getByTestId('settings-database')).toHaveTextContent('healthy');
    expect(screen.getByTestId('health-database')).toHaveTextContent('12 ms');
    // judge settings declared as env-managed, never fabricated values
    expect(screen.getByTestId('settings-judge')).toHaveTextContent('由环境变量');
    expect(screen.getByTestId('settings-judge')).toHaveTextContent('JUDGE_PROVIDER');
    // no fake save button anywhere
    expect(screen.queryByRole('button', { name: /保存|Save/ })).not.toBeInTheDocument();
  });
});

// ---------------- EvaluationDetailPage: evidence chain ----------------

describe('评估工作台 — Evidence 链（G2/G3）', () => {
  it('抽屉渲染完整 I/O + Evidence items（不再只显示 contract 字符串）', async () => {
    stubFetch((url) => {
      if (url.includes('/quality-gate')) return { body: fx.qualityGate('PASS') };
      if (/\/api\/evaluations\/run-aaaa-1111$/.test(url)) return { body: fx.runDetail() };
      if (url.includes('/report')) return { body: fx.report() };
      if (url.includes('/diagnoses')) return { body: fx.diagnosesWithEvidence() };
      if (url.includes('/results?')) {
        return {
          body: {
            items: [
              {
                id: 'res-2', record_id: 'r2', row_index: 1,
                question: '2024年营收是多少？', answer: '1200亿元',
                reference_answer: '1000亿元', is_failure: true,
                created_at: '2026-08-30T01:01:00Z',
              },
            ],
            total: 1, page: 1, page_size: 100,
          },
        };
      }
      if (url.includes('/recommendations')) return { body: fx.recommendations() };
      if (url.includes('/results/res-2')) return { body: fx.resultDetail() };
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    });
    render(
      <MemoryRouter>
        <ProjectProvider>
          <EvaluationDetailPage runId="run-aaaa-1111" />
        </ProjectProvider>
      </MemoryRouter>,
    );
    await settled();

    fireEvent.click(screen.getByTestId('tab-failures'));
    const sample = await screen.findByTestId('failure-sample');
    fireEvent.click(sample);

    const drawer = await screen.findByTestId('diagnosis-drawer');
    // G3: full contexts + reference I/O
    expect(drawer).toHaveTextContent('检索上下文 A');
    expect(drawer).toHaveTextContent('2024年度公司营业收入为1000亿元');
    // G2: typed evidence items
    expect(screen.getByTestId('evidence-reference_evidence')).toBeInTheDocument();
    expect(screen.getByTestId('evidence-answer_claim')).toBeInTheDocument();
    expect(screen.getByTestId('evidence-contract')).toHaveTextContent('integrity.numerical_mismatch.v1');
    // comparison_basis present in metrics section
    expect(drawer).toHaveTextContent('comparison_basis');
  });
});
