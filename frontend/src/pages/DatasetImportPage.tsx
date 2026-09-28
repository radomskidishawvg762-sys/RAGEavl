/**
 * DatasetImportPage — 导入数据集（Phase 4）。
 * 流程：选择文件 → 数据预览 → 检查结果 → 导入完成。
 * 依据后端同步 API：P0 仅展示阶段 UI，不伪造百分比进度。
 * 校验语义完全来自后端（POST .../datasets:import 的结果或错误上下文），
 * 前端不自行判定 validation。
 */

import { useCallback, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { DragEvent } from 'react';

import { api, endpoints, ApiError } from '../api/client';
import type { DatasetImportOut, DatasetValidationResponse } from '../api/types';
import { useProject } from '../context/ProjectContext';
import { PageHeader, Panel, Tag } from '../components/primitives/Surfaces';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { ValidationReport } from '../components/dataset/ValidationReport';
import { formatBytes } from '../utils/format';
import styles from './DatasetImportPage.module.css';

type Step = 1 | 2 | 3 | 4;

/** 「创建新版本」入口带来的提示参数：?versionOf=<dataset name>（FR-03）。 */
const VERSION_OF_PARAM = 'versionOf';

interface Parsed {
  name: string;
  domain: string;
  policy: string;
  records: unknown[];
}

const STEPS: Array<[number, string]> = [
  [1, '选择文件'],
  [2, '数据预览'],
  [3, '检查结果'],
  [4, '导入完成'],
];

export function DatasetImportPage() {
  const { activeProject, error: projectError } = useProject();
  const [step, setStep] = useState<Step>(1);
  const [fileName, setFileName] = useState('');
  const [fileSize, setFileSize] = useState(0);
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [success, setSuccess] = useState<DatasetImportOut | null>(null);
  const [validation, setValidation] = useState<DatasetValidationResponse | null>(null);
  const [importError, setImportError] = useState<unknown>(null);
  const [dragOver, setDragOver] = useState(false);
  const [searchParams] = useSearchParams();
  const versionOf = searchParams.get(VERSION_OF_PARAM);

  const handleFile = useCallback((file: File) => {
    setFileName(file.name);
    setFileSize(file.size);
    setJsonError(null);
    setParsed(null);
    setValidation(null);
    setSuccess(null);
    setImportError(null);
    void file.text().then((text) => {
      try {
        const data = JSON.parse(text) as Record<string, unknown>;
        if (!data || typeof data !== 'object') throw new Error('envelope must be an object');
        const records = Array.isArray(data.records) ? data.records : [];
        setParsed({
          name: typeof data.name === 'string' ? data.name : '(未命名)',
          domain: typeof data.domain === 'string' ? data.domain : 'general',
          policy: typeof data.duplicate_policy === 'string' ? data.duplicate_policy : 'strict',
          records,
        });
        setStep(2);
      } catch (e) {
        setJsonError(e instanceof Error ? e.message : '无法解析 JSON');
        setStep(2);
      }
    });
  }, []);

  const onDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const doImport = useCallback(async () => {
    if (!activeProject || !parsed) return;
    setImporting(true);
    setImportError(null);
    try {
      const result = await api.post<DatasetImportOut>(endpoints.datasetImport(activeProject.id), {
        name: parsed.name,
        domain: parsed.domain,
        duplicate_policy: parsed.policy,
        records: parsed.records,
      });
      setSuccess(result);
      setValidation(result.validation_report ?? null);
      setStep(4);
    } catch (e) {
      setImportError(e);
      if (e instanceof ApiError) {
        const report = (e.context?.validation_report ?? null) as DatasetValidationResponse | null;
        setValidation(report);
      }
      setStep(3);
    } finally {
      setImporting(false);
    }
  }, [activeProject, parsed]);

  if (!activeProject) {
    return (
      <EmptyState
        title="请选择项目"
        description="导入数据集需要指定所属项目。请先选择项目。"
        icon="circle"
      />
    );
  }
  if (projectError) return <ErrorState error={projectError} />;

  return (
    <>
      <PageHeader
        breadcrumb={<><span>数据集</span><span>/</span><span>导入</span></>}
        title="导入数据集"
        subtitle={`目标项目：${activeProject.name}`}
      />

      <StepBar current={step} />

      {versionOf ? (
        <div className={styles.versionHint} data-testid="version-of-hint">
          为数据集 <strong>{versionOf}</strong> 创建新版本：请导入同名（name={versionOf}）的 JSON 文件，
          后端将自动生成新版本。
        </div>
      ) : null}

      <div className={styles.content}>
        {step === 1 ? (
          <Panel title="选择文件">
            <div
              className={[styles.dropzone, dragOver ? styles.dragOver : ''].join(' ')}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              data-testid="dropzone"
            >
              <div className={styles.dropHint}>拖拽 JSON 文件到此处，或点击选择文件</div>
              <label className={styles.pickBtn}>
                选择文件
                <input
                  type="file"
                  accept="application/json,.json"
                  className={styles.fileInput}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleFile(f);
                  }}
                />
              </label>
              <div className={styles.dropNote}>支持含 name 与 records 字段的 JSON 文件。</div>
            </div>
          </Panel>
        ) : null}

        {step === 2 ? (
          <Panel title="数据预览">
            <div className={styles.preview}>
              <PreviewRow label="文件名" value={fileName} />
              <PreviewRow label="文件大小" value={formatBytes(fileSize)} />
              <PreviewRow label="预计记录数" value={String(parsed?.records.length ?? 0)} />
              <PreviewRow
                label="JSON 格式"
                value={jsonError ? '无效' : '有效'}
                tone={jsonError ? 'error' : 'pass'}
              />
              {parsed ? <PreviewRow label="数据集名称" value={parsed.name} /> : null}
            </div>
            {jsonError ? <div className={styles.jsonError}>JSON 解析失败：{jsonError}</div> : null}
            <div className={styles.actions}>
              <button type="button" className={styles.btn} onClick={() => setStep(1)}>
                重新选择
              </button>
              <button
                type="button"
                className={[styles.btn, styles.primary].join(' ')}
                disabled={!!jsonError || importing}
                onClick={doImport}
                data-testid="do-import"
              >
                {importing ? '正在导入…' : '开始校验并导入'}
              </button>
            </div>
          </Panel>
        ) : null}

        {step === 3 ? (
          <Panel title="检查结果" accent={validation ? 'warning' : 'neutral'}>
            {importing && !importError ? <LoadingState label="正在导入…" /> : null}
            {importError ? (
              <div className={styles.failBanner}>
                <Tag tone="error">校验失败</Tag>
                <span className={styles.failNote}>请修正数据后重新导入。无效数据集不会被当作成功导入。</span>
              </div>
            ) : null}
            {validation ? (
              <div className={styles.validationBlock}>
                <ValidationReport validation={validation} />
              </div>
            ) : null}
            {importError && !validation ? <ErrorState error={importError} /> : null}
            <div className={styles.actions}>
              <button type="button" className={styles.btn} onClick={() => setStep(2)}>
                返回
              </button>
              <button type="button" className={styles.btn} onClick={() => setStep(1)}>
                重新选择文件
              </button>
            </div>
          </Panel>
        ) : null}

        {step === 4 ? (
          <Panel title="导入完成" accent="pass">
            {success ? (
              <div className={styles.done} data-testid="import-success">
                <div className={styles.doneHeader}>
                  <span className={styles.doneIcon}>✓</span>
                  <span>导入成功</span>
                </div>
                <div className={styles.preview}>
                  <PreviewRow label="数据集名称" value={success.name} />
                  <PreviewRow label="版本" value={success.version} />
                  <PreviewRow label="记录数" value={String(success.record_count)} />
                  <PreviewRow
                    label="校验状态"
                    value={success.validation_status === 'valid' ? '有效' : '无效'}
                    tone={success.validation_status === 'valid' ? 'pass' : 'error'}
                  />
                </div>
                {validation && validation.checks.length ? (
                  <div className={styles.validationBlock}>
                    <ValidationReport validation={validation} />
                  </div>
                ) : null}
                <div className={styles.actions}>
                  <Link className={styles.btn} to={`/datasets/${success.id}`}>
                    查看数据集详情
                  </Link>
                  <Link className={styles.btn} to="/datasets">
                    返回数据集列表
                  </Link>
                </div>
              </div>
            ) : (
              <EmptyState title="导入完成" description="数据已成功纳入评估体系。" icon="check" />
            )}
          </Panel>
        ) : null}
      </div>
    </>
  );
}

function StepBar({ current }: { current: Step }) {
  return (
    <div className={styles.steps} data-testid="import-steps">
      {STEPS.map(([n, label]) => {
        const state = n < current ? 'done' : n === current ? 'active' : 'todo';
        return (
          <div key={n} className={styles.step} data-state={state}>
            <span className={styles.stepDot}>{n < current ? '✓' : n}</span>
            <span className={styles.stepLabel}>{label}</span>
          </div>
        );
      })}
    </div>
  );
}

function PreviewRow({ label, value, tone }: { label: string; value: string; tone?: 'pass' | 'error' }) {
  return (
    <div className={styles.previewRow}>
      <span className={styles.previewLabel}>{label}</span>
      <span className={styles.previewValue} data-tone={tone}>{value}</span>
    </div>
  );
}
