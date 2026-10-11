import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ProjectProvider, useProject } from '../src/context/ProjectContext';
import { NewEvaluationPage } from '../src/pages/NewEvaluationPage';
import * as fx from './fixtures';

beforeEach(() => window.localStorage.clear());

function SwitchProject() {
  const { setActiveProjectId } = useProject();
  return <button onClick={() => setActiveProjectId('p2')}>切换到项目二</button>;
}

function setup(datasetOverrides: Record<string, unknown> = {}, total = 1) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    let body: unknown = {};
    if (url.includes('/configs/profiles')) body = fx.profileList();
    else if (url.includes('/metrics')) body = fx.metricList();
    else if (/\/api\/projects\/[^/]+\/configs/.test(url)) body = { items: [], total: 0 };
    else if (url.includes('/api/projects')) body = fx.projectList(2);
    else if (url.includes('/api/datasets')) {
      const params = new URL(url, 'http://localhost').searchParams;
      const pid = params.get('project_id') ?? 'p1';
      const page = Number(params.get('page') ?? 1);
      body = {
        items: [{ ...fx.dataset(), ...datasetOverrides, project_id: pid, id: 'ds-' + pid, name: page === 1 ? '第一页数据集' : '第二页数据集' }],
        total, page, page_size: 20,
      };
    }
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal('fetch', fetchMock);
  render(
    <MemoryRouter><ProjectProvider><SwitchProject /><NewEvaluationPage /></ProjectProvider></MemoryRouter>,
  );
  return fetchMock;
}

describe('评估创建入口', () => {
  it.each([
    { validation_status: 'invalid', record_count: 3 },
    { validation_status: 'valid', record_count: 0 },
  ])('校验失败或空数据集不能进入后续步骤：%j', async (dataset) => {
    setup(dataset);
    const option = await screen.findByTestId('select-dataset');
    expect(option).toBeDisabled();
    fireEvent.click(option);
    expect(screen.getByRole('button', { name: '下一步' })).toBeDisabled();
    expect(screen.getByRole('link', { name: '查看校验报告' })).toHaveAttribute('href', '/datasets/ds-p1');
  });

  it('切换项目后清空旧数据集、配置和向导进度', async () => {
    setup();
    fireEvent.click(await screen.findByTestId('select-dataset'));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    fireEvent.click(await screen.findByTestId('select-profile'));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    expect(screen.getByTestId('metric-editor')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '切换到项目二' }));

    await waitFor(() => expect(screen.queryByTestId('metric-editor')).not.toBeInTheDocument());
    expect(await screen.findByTestId('select-dataset')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一步' })).toBeDisabled();
  });

  it('超过 20 个数据集时可以翻页选择，而不是截断候选项', async () => {
    setup({}, 21);
    expect(await screen.findByText('第一页数据集')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('第二页数据集')).toBeInTheDocument();
    expect(screen.getByText('第 2 / 2 页 · 共 21 个')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('select-dataset'));
    expect(screen.getByRole('button', { name: '下一步' })).toBeEnabled();
  });

});
