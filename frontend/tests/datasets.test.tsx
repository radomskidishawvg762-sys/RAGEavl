/**
 * Phase 4 Dataset tests — 数据集 / 详情 / 导入 / 校验 / 记录浏览。UI 中文，
 * 校验语义来自后端。fetch 被 stub，无真实后端。
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { DatasetsPage } from '../src/pages/DatasetsPage';
import { DatasetDetailPage } from '../src/pages/DatasetDetailPage';
import { DatasetImportPage } from '../src/pages/DatasetImportPage';
import { ProjectProvider } from '../src/context/ProjectContext';
import { presentMetric, presentRun } from '../src/status/status';
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

// ---------------- 数据集列表 ----------------

describe('数据集列表', () => {
  function handler(url: string, init?: RequestInit): { status?: number; body: unknown } | undefined {
    void init;
    if (url.includes('/api/projects')) return { body: fx.projectList(1) };
    if (url.includes('/api/datasets')) return { body: fx.datasetList(3) };
    return undefined;
  }

  it('展示数量、行、版本、校验/锁定状态、创建时间', async () => {
    stubFetch(handler);
    await renderWithProject(<DatasetsPage />);
    await waitFor(() => expect(screen.getByText(/共 3 个数据集/)).toBeInTheDocument());
    expect(screen.getAllByText('数据集').length).toBeGreaterThan(0);
    expect(screen.getAllByTestId('dataset-row').length).toBe(3);
    expect(screen.getAllByText('v1').length).toBeGreaterThan(0);
    expect(screen.getAllByText('有效').length).toBeGreaterThan(0);
    expect(screen.getAllByText('未锁定').length).toBeGreaterThan(0);
    expect(screen.getAllByText('查看').length).toBeGreaterThan(0);
    expect(screen.getAllByTestId('preview-financial-qa-0').length).toBe(1);
    expect(screen.getAllByTestId('validate-financial-qa-0').length).toBe(1);
    expect(screen.getAllByText('导入数据集').length).toBeGreaterThan(0);
  });

  it('请求作用域包含 project_id', async () => {
    const fetchMock = stubFetch(handler);
    await renderWithProject(<DatasetsPage />);
    await waitFor(() => expect(screen.getAllByTestId('dataset-row').length).toBeGreaterThan(0));
    const dsUrl = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/datasets?'))?.[0];
    expect(String(dsUrl)).toContain('project_id=p1');
  });

  it('空状态下显示「暂无数据集」', async () => {
    stubFetch((url) =>
      url.includes('/api/projects')
        ? { body: fx.projectList(1) }
        : { body: { items: [], total: 0, page: 1, page_size: 20 } },
    );
    await renderWithProject(<DatasetsPage />);
    await waitFor(() => expect(screen.getByText('暂无数据集')).toBeInTheDocument());
  });

  it('API 失败显示友好错误', async () => {
    stubFetch(() => ({ status: 503, body: { detail: 'x', code: 'EXT_DB_UNAVAILABLE', trace_id: 't' } }));
    await renderWithProject(<DatasetsPage />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('数据库暂时不可用');
  });
});

// ---------------- 数据集详情 ----------------

describe('数据集详情', () => {
  function handler(url: string): { status?: number; body: unknown } | undefined {
    if (url.includes('/api/projects')) return { body: fx.projectList(1) };
    if (url.includes('/validation')) return { body: fx.datasetValidationEndpoint(true) };
    if (url.includes('/records')) return { body: fx.datasetRecords(2) };
    return { body: fx.dataset(false) };
  }

  it('渲染概览卡片，Validation / Records 分 Tab 呈现', async () => {
    stubFetch(handler);
    await renderWithProject(<DatasetDetailPage datasetId="ds-1111" />);
    // Overview Tab（默认）：元数据卡片
    await waitFor(() => expect(screen.getByText('版本 Version')).toBeInTheDocument());
    // Validation Tab：校验报告
    fireEvent.click(screen.getByTestId('tab-validation'));
    expect(screen.getByTestId('validation-summary')).toBeInTheDocument();
    // Records Tab：记录 DataTable
    fireEvent.click(screen.getByTestId('tab-records'));
    expect(screen.getAllByTestId('record-row').length).toBe(2);
  });

  it('校验失败展示 5 类检查与 row_index 定位', async () => {
    stubFetch((url) => {
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      if (url.includes('/validation')) return { body: fx.datasetValidationEndpoint(false, true) };
      if (url.includes('/records')) return { body: fx.datasetRecords(0) };
      return { body: fx.dataset(false) };
    });
    await renderWithProject(<DatasetDetailPage datasetId="ds-1111" />);
    fireEvent.click(await screen.findByTestId('tab-validation'));
    await waitFor(() => expect(screen.getByText('校验失败')).toBeInTheDocument());
    expect(screen.getByText('结构校验')).toBeInTheDocument();
    expect(screen.getByText('缺失字段检测')).toBeInTheDocument();
    expect(screen.getByText('引用有效性校验')).toBeInTheDocument();
    expect(screen.getByText('领域元数据校验')).toBeInTheDocument();
    // row_index 定位信息不可隐藏
    expect(screen.getAllByTestId('issue-row-index').length).toBeGreaterThan(0);
    expect(screen.getByText('#142')).toBeInTheDocument();
  });

  it('点击记录打开详情抽屉，长文本可展开', async () => {
    const long = '非常长的'.repeat(60); // > 阈值
    stubFetch((url) => {
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      if (url.includes('/validation')) return { body: fx.datasetValidationEndpoint(true) };
      if (url.includes('/records')) {
        return {
          body: { items: [{ row_index: 0, question: long, reference_answer: '参考答案', reference_contexts: [], metadata: {} }], total: 1, page: 1, page_size: 20 },
        };
      }
      return { body: fx.dataset(false) };
    });
    await renderWithProject(<DatasetDetailPage datasetId="ds-1111" />);
    fireEvent.click(await screen.findByTestId('tab-records'));
    const row = await screen.findByTestId('record-row');
    fireEvent.click(row);
    fireEvent.click(screen.getByRole('button', { name: '查看' }));
    const drawer = await screen.findByRole('dialog');
    expect(drawer).toHaveTextContent('记录详情');
    expect(drawer).toHaveTextContent('展开全部');
    expect(drawer).not.toHaveTextContent(long); // 长文本默认截断
    fireEvent.click(screen.getByText('展开全部'));
    expect(drawer).toHaveTextContent(long);
  });
});


// ---------------- 数据集导入 ----------------

describe('数据集导入', () => {
  const envelope = { name: 'financial-qa', domain: 'general', duplicate_policy: 'strict', records: [{ question: 'q' }] };
  const fakeFile = {
    name: 'financial-qa.json',
    size: 1024,
    text: async () => JSON.stringify(envelope),
  } as unknown as File;

  it('选择文件→预览→校验导入→导入完成（无伪造进度）', async () => {
    stubFetch((url, init) => {
      if (url.includes('/datasets:import')) {
        expect(init?.method).toBe('POST');
        return { body: fx.datasetImportResult(true), status: 201 };
      }
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    });
    await renderWithProject(<DatasetImportPage />);
    expect(screen.getAllByText('选择文件').length).toBeGreaterThan(0);
    // 模拟选择文件（拖拽/选择均走同一 handleFile）
    fireEvent.change(screen.getByLabelText('选择文件'), { target: { files: [fakeFile] } });
    await waitFor(() => expect(screen.getByText('预计记录数')).toBeInTheDocument());
    expect(screen.getByText('financial-qa.json')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument(); // 预计记录数
    fireEvent.click(screen.getByTestId('do-import'));
    await waitFor(() => expect(screen.getByTestId('import-success')).toBeInTheDocument());
    expect(screen.getByText('导入成功')).toBeInTheDocument();
    // 不伪造百分进度：没有“37%”
    expect(screen.queryByText(/37%/)).toBeNull();
  });

  it('校验失败显示失败原因与校验报告', async () => {
    stubFetch((url) => {
      if (url.includes('/datasets:import')) {
        return {
          status: 400,
          body: {
            detail: 'dataset validation failed',
            code: 'BIZ_VALIDATION_FAILED',
            trace_id: 't-1',
            context: { validation_report: fx.datasetValidation(false, true) },
          },
        };
      }
      if (url.includes('/api/projects')) return { body: fx.projectList(1) };
      return undefined;
    });
    await renderWithProject(<DatasetImportPage />);
    fireEvent.change(screen.getByLabelText('选择文件'), { target: { files: [fakeFile] } });
    await waitFor(() => expect(screen.getByText('预计记录数')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('do-import'));
    await waitFor(() => expect(screen.getAllByText('校验失败').length).toBeGreaterThan(0));
    expect(screen.getByText('缺失字段检测')).toBeInTheDocument();
    // 无效数据集不被当作成功导入
    expect(screen.queryByTestId('import-success')).toBeNull();
  });
});

// ---------------- 状态中文化映射 ----------------

describe('状态中文映射', () => {
  it('run 状态中文', () => {
    expect(presentRun('completed').label).toBe('已完成');
    expect(presentRun('running').label).toBe('运行中');
    expect(presentRun('failed').label).toBe('执行失败');
    expect(presentRun('cancelled').label).toBe('已取消');
  });

  it('metric 状态中文', () => {
    expect(presentMetric('completed', false).label).toBe('质量失败');
    expect(presentMetric('undetermined', null).label).toBe('无法判定');
    expect(presentMetric('not_configured', null).label).toBe('未配置');
    expect(presentMetric('error', null).label).toBe('执行错误');
  });
});
