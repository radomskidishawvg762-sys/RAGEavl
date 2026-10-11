import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ProjectProvider, useProject } from '../src/context/ProjectContext';
import { ComparePage } from '../src/pages/ComparePage';
import { DatasetImportPage } from '../src/pages/DatasetImportPage';
import { useRunOptions } from '../src/hooks/useRunOptions';
import * as fx from './fixtures';

beforeEach(() => window.localStorage.clear());

function SwitchProject() {
  const { setActiveProjectId } = useProject();
  return <button onClick={() => setActiveProjectId('p2')}>切换项目</button>;
}

function stub() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const pid = new URL(url, 'http://localhost').searchParams.get('project_id') ?? 'p1';
    return { ok: true, status: 200, json: async () => url.includes('/api/projects')
      ? fx.projectList(2)
      : { items: [{ ...fx.runDetail(), run_id: 'run-' + pid }, { ...fx.runDetail(), run_id: 'candidate-' + pid }], total: 2, page: 1, page_size: 100 } };
  }));
}

it('切换项目后对比选择不能继续引用旧项目运行', async () => {
  stub();
  render(<MemoryRouter><ProjectProvider><SwitchProject /><ComparePage /></ProjectProvider></MemoryRouter>);
  await waitFor(() => expect(within(screen.getByTestId('baseline-select')).getByRole('option', { name: /run-p1/ })).toBeInTheDocument());
  const baseline = screen.getByTestId('baseline-select');
  fireEvent.change(baseline, { target: { value: 'run-p1' } });
  fireEvent.change(screen.getByTestId('candidate-select'), { target: { value: 'candidate-p1' } });
  expect(screen.getByTestId('compare-submit')).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '切换项目' }));
  await waitFor(() => expect(within(screen.getByTestId('baseline-select')).getByRole('option', { name: /run-p2/ })).toBeInTheDocument());
  expect(screen.getByTestId('baseline-select')).toHaveValue('');
  expect(screen.getByTestId('compare-submit')).toBeDisabled();
});

it('导入预览随项目切换清空，避免把旧项目文件误导入新项目', async () => {
  stub();
  const { container } = render(<MemoryRouter><ProjectProvider><SwitchProject /><DatasetImportPage /></ProjectProvider></MemoryRouter>);
  await screen.findByTestId('dropzone');
  const file = new File([''], 'sample.json', { type: 'application/json' });
  Object.defineProperty(file, 'text', { value: async () => JSON.stringify({ name: 'old-project-data', records: [] }) });
  fireEvent.change(container.querySelector('input[type=file]')!, { target: { files: [file] } });
  await screen.findByTestId('do-import');
  fireEvent.click(screen.getByRole('button', { name: '切换项目' }));
  expect(await screen.findByTestId('dropzone')).toBeInTheDocument();
  expect(screen.queryByTestId('do-import')).not.toBeInTheDocument();
});

it('运行选择器包含第 101 条运行，不静默截断在第一页', async () => {
  const pages: number[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const page = Number(new URL(String(input), 'http://localhost').searchParams.get('page'));
    pages.push(page);
    return { ok: true, status: 200, json: async () => ({
      items: Array.from({ length: page === 1 ? 100 : 1 }, (_, i) => ({ ...fx.runDetail(), run_id: `run-${(page - 1) * 100 + i}` })),
      total: 101, page, page_size: 100,
    }) };
  }));
  const { result } = renderHook(() => useRunOptions('p1'));
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(result.current.state).toBe('ready'));
  expect(result.current.runs).toHaveLength(101);
  expect(result.current.runs[result.current.runs.length - 1]?.runId).toBe('run-100');
  expect(pages).toEqual([1, 2]);
});
