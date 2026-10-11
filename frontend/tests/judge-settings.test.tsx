import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SettingsPage } from '../src/pages/SettingsPage';

const config = { provider: 'openai', model: 'saved', model_version: '', base_url: '', temperature: 0,
  max_tokens: 1024, timeout: 30, retry: 3, api_key_configured: true, configured: true, source: 'environment' };

it('测试连接发送当前草稿，返回失败时展示具体原因，不保存草稿', async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); requests.push({ url, init });
    const body = url.endsWith('/test')
      ? { ...config, test_status: 'failed', reachable: false, error_code: 'EXT_JUDGE_PERMISSION_DENIED',
        message: '当前账号或套餐无权调用该模型，请更换可用模型或检查权限。', latency_ms: 12 }
      : url.endsWith('/judge') ? config : { app: 'healthy', database: 'healthy', detail: { db_latency_ms: 2 } };
    return { ok: true, status: 200, json: async () => body } as Response;
  }));
  render(<SettingsPage />);
  const model = await screen.findByLabelText('模型 Model');
  fireEvent.change(model, { target: { value: 'draft-model' } });
  fireEvent.change(screen.getByLabelText('临时 API Key'), { target: { value: 'draft-secret' } });
  fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
  await screen.findByText(/当前账号或套餐无权调用该模型/);
  const probe = requests.filter(r => r.url.endsWith('/test'));
  expect(probe).toHaveLength(1);
  expect(JSON.parse(String(probe[0].init?.body))).toMatchObject({ model: 'draft-model', api_key: 'draft-secret' });
  expect(requests.filter(r => r.init?.method === 'PUT')).toHaveLength(0);
  expect(screen.queryByText('draft-secret')).not.toBeInTheDocument();
});

it('连接成功显示真实耗时，修改草稿会清除过期的成功提示', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.endsWith('/test') ? { ...config, test_status: 'success', reachable: true,
      message: '连接成功，模型已返回有效文本。', latency_ms: 42 }
      : url.endsWith('/judge') ? config : { app: 'healthy', database: 'healthy' };
    return { ok: true, status: 200, json: async () => body } as Response;
  }));
  render(<SettingsPage />);
  fireEvent.click(await screen.findByRole('button', { name: '测试连接' }));
  await screen.findByText(/连接成功.*42 ms/);
  fireEvent.change(screen.getByLabelText('模型 Model'), { target: { value: 'changed' } });
  await waitFor(() => expect(screen.queryByText(/连接成功/)).not.toBeInTheDocument());
});
