/**
 * Shared fixtures mirroring the backend schemas (snake_case, as returned by
 * the API). Tests never recompute backend semantics — they only assert what
 * the frontend renders.
 */

import type {
  DiagnosesPageResponse,
  ProjectListResponse,
  ProjectSummaryResponse,
  RecommendationsResponse,
  ReportResponse,
  ResultDetailOut,
  ResultsPageResponse,
  RegressionResponse,
  ComparisonResponse,
  RunListResponse,
  RunOut,
  DatasetListResponse,
  DatasetOut,
  DatasetImportOut,
  DatasetRecordsPage,
  DatasetValidationResponse,
  ProgressResponse,
  QualityGateResponse,
  CreateRunResponse,
  MetricListResponse,
  ProfileListResponse,
  SaveConfigResponse,
} from '../src/api/types';

export function runList(total = 2): RunListResponse {
  return {
    items: [
      {
        run_id: 'run-aaaa-1111',
        project_id: 'p1',
        dataset_id: 'ds-1111',
        config_id: 'cfg-1111',
        status: 'completed',
        total_records: 10,
        evaluated_records: 10,
        error_records: 0,
        evaluation_coverage: 1,
        overall_score: 0.82,
        created_at: '2026-08-30T01:00:00Z',
        started_at: '2026-08-30T01:00:01Z',
        finished_at: '2026-08-30T01:01:00Z',
        reproducibility_meta: { dataset_version: 'v1' },
        error_summary: null,
      },
      {
        run_id: 'run-bbbb-2222',
        project_id: 'p2',
        dataset_id: 'ds-2222',
        config_id: 'cfg-2222',
        status: 'completed',
        total_records: 8,
        evaluated_records: 8,
        error_records: 0,
        evaluation_coverage: 1,
        // no valid score -> the UI must say "No valid metric score", never 0
        overall_score: null,
        created_at: '2026-08-29T01:00:00Z',
        started_at: null,
        finished_at: null,
        reproducibility_meta: { dataset_version: 'v2' },
        error_summary: null,
      },
    ].slice(0, total) as RunListResponse['items'],
    total,
    page: 1,
    page_size: 20,
  };
}

export function runDetail(): RunOut {
  return {
    run_id: 'run-aaaa-1111',
    project_id: 'p1',
    dataset_id: 'ds-1111',
    config_id: 'cfg-1111',
    status: 'completed',
    total_records: 10,
    evaluated_records: 10,
    error_records: 0,
    evaluation_coverage: 1,
    overall_score: 0.5,
    created_at: '2026-08-30T01:00:00Z',
    started_at: '2026-08-30T01:00:01Z',
    finished_at: '2026-08-30T01:01:00Z',
    reproducibility_meta: { dataset_version: 'v1', config_version: 'cfg-abc', judge_model: 'gpt-4o-mini', judge_model_version: 'gpt-4o-mini-2024-07-18' },
    error_summary: null,
  };
}

export function projectList(total = 1): ProjectListResponse {
  return {
    items: Array.from({ length: total }, (_, i) => ({
      id: `p${i + 1}`,
      name: `project-${i + 1}`,
      domain: 'general',
      status: 'active',
      created_at: '2026-08-28T10:00:00Z',
    })),
    total,
    page: 1,
    page_size: 20,
  };
}

export function report(): ReportResponse {
  return {
    summary: {
      run_id: 'run-aaaa-1111',
      status: 'completed',
      is_final: true,
      overall_score: 0.5,
      total_records: 2,
      evaluated_records: 2,
      error_records: 0,
      evaluation_coverage: 1,
      valid_metric_count: 2,
      total_enabled_metric_count: 3,
      input_mode: 'golden_replay',
      created_at: '2026-08-30T01:00:00Z',
      started_at: '2026-08-30T01:00:01Z',
      finished_at: '2026-08-30T01:01:00Z',
      message: null,
    },
    metrics: [
      {
        name: 'temporal_consistency',
        category: 'integrity',
        score: 1,
        threshold: null,
        passed: null,
        status: 'completed',
        valid_count: 2,
        invalid_count: 0,
        error_count: 0,
        metric_version: 'integrity-normalization-v1',
        weight: 1,
      },
      {
        name: 'numerical_consistency',
        category: 'integrity',
        score: 0,
        threshold: 0.9,
        passed: false,
        status: 'completed',
        valid_count: 2,
        invalid_count: 0,
        error_count: 0,
        metric_version: 'integrity-normalization-v1',
        weight: 2,
      },
      {
        name: 'faithfulness',
        category: 'generation',
        score: null,
        threshold: null,
        passed: null,
        status: 'not_configured',
        valid_count: 0,
        invalid_count: 2,
        error_count: 2,
        metric_version: 'ragas-0.4.3',
        weight: 1,
      },
    ],
    raw_metrics: [],
    failures: [
      {
        diagnosis_id: 'd1',
        record_id: 'r2',
        question: '2024年营收是多少？',
        related_metric: 'numerical_consistency',
        failure_type: 'integrity.numerical_mismatch',
        severity: 'CRITICAL',
        root_cause: 'Numerical Mismatch (abs_diff=1e8)',
        status: 'failure',
        confidence: 'high',
      },
    ],
    undetermined: [
      {
        diagnosis_id: 'd2',
        record_id: 'r3',
        related_metric: 'temporal_consistency',
        status: 'undetermined',
        reason: 'comparison_type=ambiguous: cannot reliably judge',
        missing_evidence: ['reference_evidence'],
      },
    ],
    execution_errors: [
      {
        record_id: 'r4',
        metric: 'faithfulness',
        error_code: 'EXT_RAG_ADAPTER_TIMEOUT',
        message: 'RAG adapter request failed: timeout',
      },
    ],
    run_level_diagnoses: { status: 'not_available', items: [] },
    quality_dimensions: [
      { dimension: 'retrieval', metrics: ['context_precision'], score: 0.91, metric_count: 1, failure_count: 0, undetermined_count: 0, diagnosed_count: 0, diagnosis_coverage: null, evidence_count: 0, evidence_coverage: null, evaluated_rows: 50, total_rows: 50, is_weakest: false },
      { dimension: 'generation', metrics: ['faithfulness'], score: 0.65, metric_count: 1, failure_count: 0, undetermined_count: 0, diagnosed_count: 0, diagnosis_coverage: null, evidence_count: 0, evidence_coverage: null, evaluated_rows: 50, total_rows: 50, is_weakest: false },
      { dimension: 'groundedness', metrics: ['faithfulness'], score: 0.65, metric_count: 1, failure_count: 0, undetermined_count: 0, diagnosed_count: 0, diagnosis_coverage: null, evidence_count: 0, evidence_coverage: null, evaluated_rows: 50, total_rows: 50, is_weakest: false },
      { dimension: 'correctness', metrics: ['numerical_consistency'], score: 0.58, metric_count: 1, failure_count: 1, undetermined_count: 1, diagnosed_count: 1, diagnosis_coverage: 1, evidence_count: 1, evidence_coverage: 1, evaluated_rows: 36, total_rows: 50, is_weakest: true },
    ],
    reproducibility: {
      dataset_version: 'v1',
      config_version: 'cfg-abc',
      metric_version: 'integrity-normalization-v1',
      judge_model: 'gpt-4o-mini',
      judge_model_version: 'gpt-4o-mini-2024-07-18',
      judge_temperature: 0,
      judge_max_tokens: 1024,
      judge_timeout: 30,
      judge_retry: 3,
      // a hypothetical secret-shaped key must never be rendered
      judge_api_key: 'sk-SHOULD-NEVER-APPEAR',
    },
    generated_at: '2026-08-30T01:02:00Z',
  };
}

export function results(): ResultsPageResponse {
  return {
    items: [
      {
        id: 'res-1',
        record_id: 'r1',
        row_index: 0,
        question: 'q',
        answer: 'a',
        reference_answer: 'ref',
        is_failure: false,
        created_at: '2026-08-30T01:01:00Z',
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  };
}

export function diagnoses(): DiagnosesPageResponse {
  return { items: [], total: 0, page: 1, page_size: 50 };
}

export function diagnosesWithEvidence(): DiagnosesPageResponse {
  return {
    items: [
      {
        id: 'd1',
        result_id: 'res-2',
        status: 'diagnosed',
        failure_type: 'integrity.numerical_mismatch',
        related_metric: 'numerical_consistency',
        root_cause: '生成阶段数值与参考上下文不一致',
        severity: 'CRITICAL',
        evidence_contract: 'integrity.numerical_mismatch.v1',
        evidence: [
          {
            type: 'reference_evidence',
            source: 'reference_contexts',
            locator: 'record.reference_contexts[0]',
            content: '2024年度公司营业收入为1000亿元',
            metadata: { normalized: { raw: '1000亿元' } },
          },
          {
            type: 'answer_claim',
            source: 'answer',
            locator: 'claim[0]',
            content: '2024年公司营业收入为1200亿元',
            metadata: { normalized: { raw: '1200亿元', value: 1200 } },
          },
        ],
        confidence: 'high',
        detail: null,
        created_at: '2026-08-30T01:01:30Z',
      },
    ],
    total: 1,
    page: 1,
    page_size: 50,
  };
}

export function resultDetail(): ResultDetailOut {
  return {
    id: 'res-2',
    run_id: 'run-aaaa-1111',
    record_id: 'r2',
    row_index: 1,
    question: '2024年营收是多少？',
    answer: '2024年公司营业收入为1200亿元',
    contexts: ['检索上下文 A', '检索上下文 B'],
    reference_answer: '2024年度公司营业收入为1000亿元',
    reference_contexts: ['2024年度公司营业收入为1000亿元'],
    is_failure: true,
    created_at: '2026-08-30T01:01:00Z',
    metric_results: [
      {
        name: 'numerical_consistency',
        category: 'integrity',
        score: 0,
        threshold: 0.9,
        passed: false,
        status: 'completed',
        metric_version: 'integrity-normalization-v1',
        comparison_basis: {
          reference: { primary: { raw: '1000亿元', value: 1000 } },
          answer: { primary: { raw: '1200亿元', value: 1200 } },
          comparison_type: 'value_mismatch',
          method: 'deterministic',
          tolerance_applied: null,
          diff: { absolute: 200 },
        },
        error: null,
      },
    ],
    diagnoses: diagnosesWithEvidence().items,
  };
}

export function recommendations(): RecommendationsResponse {
  return {
    items: [
      {
        id: 'rec-1',
        diagnosis_id: 'd1',
        action: '检查数值抽取与单位归一化',
        priority: 1,
        source: 'rule',
      },
    ],
  };
}

// ---------------- Phase 2: compare / regression / project summary ----------------

export function comparison(status: 'DIRECT' | 'LIMITED' | 'BLOCKED' = 'DIRECT'): ComparisonResponse {
  return {
    baseline_run_id: 'run-aaaa-1111',
    candidate_run_id: 'run-bbbb-2222',
    comparability: {
      status,
      reasons:
        status === 'DIRECT'
          ? []
          : ['enabled metrics differ (baseline-only: faithfulness, candidate-only: context_recall)'],
    },
    metrics:
      status === 'BLOCKED'
        ? []
        : [
            {
              name: 'temporal_consistency',
              category: 'integrity',
              baseline_score: 0.8,
              candidate_score: 0.9,
              delta: 0.1,
              relative_delta: 0.125,
              baseline_status: 'completed',
              candidate_status: 'completed',
              metric_version: 'integrity-normalization-v1',
              comparable: true,
              incomparable_reason: null,
            },
            {
              name: 'numerical_consistency',
              category: 'integrity',
              baseline_score: 0.5,
              candidate_score: 0.4,
              delta: -0.1,
              relative_delta: -0.2,
              baseline_status: 'completed',
              candidate_status: 'completed',
              metric_version: 'integrity-normalization-v1',
              comparable: true,
              incomparable_reason: null,
            },
          ],
    overall: {
      baseline_overall: 0.82,
      candidate_overall: 0.79,
      delta: -0.03,
      comparable: status === 'DIRECT',
      reason: status === 'DIRECT' ? null : 'comparability is not DIRECT',
    },
    generated_at: '2026-08-31T01:00:00Z',
  };
}

export function regression(): RegressionResponse {
  return {
    baseline_run_id: 'run-aaaa-1111',
    candidate_run_id: 'run-bbbb-2222',
    comparability_status: 'DIRECT',
    metrics: [
      {
        metric: 'temporal_consistency',
        category: 'integrity',
        direction: 'higher_is_better',
        baseline_score: 0.8,
        candidate_score: 0.9,
        delta: 0.1,
        relative_delta: 0.125,
        epsilon: 0.01,
        epsilon_source: 'config',
        verdict: 'IMPROVEMENT',
      },
      {
        metric: 'numerical_consistency',
        category: 'integrity',
        direction: 'higher_is_better',
        baseline_score: 0.5,
        candidate_score: 0.4,
        delta: -0.1,
        relative_delta: -0.2,
        epsilon: 0.01,
        epsilon_source: 'config',
        verdict: 'REGRESSION',
      },
    ],
    categories: {
      integrity: {
        improvement_count: 1,
        regression_count: 1,
        stable_count: 0,
        comparable_count: 2,
        verdict: 'MIXED',
      },
    },
    trade_off: true,
    overall: {
      verdict: 'MIXED',
      reason: '检索质量提升，但数值一致性下降',
      overall_score_delta: -0.03,
    },
    generated_at: '2026-08-31T01:00:00Z',
  };
}

export function projectSummary(): ProjectSummaryResponse {
  return {
    project: {
      id: 'p1',
      name: 'project-1',
      domain: 'general',
      status: 'active',
      created_at: '2026-08-28T10:00:00Z',
    },
    dataset_count: 3,
    run_count: 5,
    latest_run: {
      run_id: 'run-aaaa-1111',
      status: 'completed',
      dataset_id: 'ds-1111',
      dataset_name: 'financial-qa',
      dataset_version: 'v1',
      overall_score: 0.82,
      total_records: 10,
      evaluated_records: 10,
      error_records: 0,
      evaluation_coverage: 1,
      created_at: '2026-08-30T01:00:00Z',
      finished_at: '2026-08-30T01:01:00Z',
    },
    latest_quality_gate: qualityGate('PASS'),
  };
}

export function dataset(locked = false): DatasetOut {
  return {
    id: 'ds-1111',
    project_id: 'p1',
    name: 'financial-qa',
    version: 'v1',
    record_count: 300,
    validation_status: 'valid',
    is_locked: locked,
    created_at: '2026-08-28T10:00:00Z',
  };
}

export function progress(status: 'running' | 'completed' = 'running'): ProgressResponse {
  return {
    status,
    total: 10,
    evaluated: status === 'completed' ? 10 : 4,
    errors: 0,
    coverage: status === 'completed' ? 1 : 0.4,
    cancelled: false,
  };
}

export function qualityGate(status: 'PASS' | 'FAIL' | 'NOT_EVALUABLE' = 'PASS'): QualityGateResponse {
  return {
    run_id: 'run-aaaa-1111',
    status,
    reasons: status === 'FAIL' ? ['QUALITY_THRESHOLD_FAILED'] : [],
    metrics: [
      { metric: 'numerical_consistency', score: 0.95, threshold: 0.9, passed: true, status: 'PASS', reason: null },
      { metric: 'entity_consistency', score: 0.7, threshold: 0.9, passed: false, status: 'FAIL', reason: null },
    ],
    overall_score: 0.82,
    evaluated_at: '2026-08-30T01:02:00Z',
  };
}

export function datasetList(total = 1): DatasetListResponse {
  return {
    items: Array.from({ length: total }, (_, i) => ({
      id: `ds-111${i}`,
      project_id: 'p1',
      name: `financial-qa-${i}`,
      version: 'v1',
      record_count: 300,
      validation_status: 'valid',
      is_locked: false,
      created_at: '2026-08-28T10:00:00Z',
    })),
    total,
    page: 1,
    page_size: 20,
  };
}

export function datasetValidation(valid = true, withIssues = false): DatasetValidationResponse {
  const issues = withIssues
    ? [
        { row_index: 142, field: 'reference_answer', detail: '' },
        { row_index: 377, field: 'reference_answer', detail: '' },
      ]
    : [];
  return {
    valid,
    checks: [
      { name: 'schema', passed: valid, count: valid ? 300 : null, issues: [] },
      { name: 'duplicate', passed: valid, count: valid ? 300 : null, issues: [] },
      { name: 'missing_field', passed: valid, count: withIssues ? 2 : null, issues },
      { name: 'reference', passed: valid, count: valid ? 300 : null, issues: [] },
      { name: 'domain_metadata', passed: valid, count: valid ? 300 : null, issues: [] },
    ],
  };
}

/** GET /api/datasets/{id}/validation 的真实包装形状（Freeze Gate 抓到的契约）。 */
export function datasetValidationEndpoint(valid = true, withIssues = false): {
  dataset_id: string;
  validation_status: string;
  validation_report: DatasetValidationResponse;
} {
  return {
    dataset_id: 'ds-1111',
    validation_status: valid ? 'valid' : 'invalid',
    validation_report: datasetValidation(valid, withIssues),
  };
}

export function datasetRecords(total = 2): DatasetRecordsPage {
  return {
    items: Array.from({ length: total }, (_, i) => ({
      row_index: i,
      question: `示例问题 ${i}？`,
      reference_answer: '参考答案',
      reference_contexts: ['参考上下文'],
      metadata: { domain: 'financial' },
    })),
    total,
    page: 1,
    page_size: 20,
  };
}

export function datasetImportResult(valid = true): DatasetImportOut {
  return {
    id: 'ds-new',
    project_id: 'p1',
    name: 'financial-qa-new',
    version: 'v1',
    record_count: 300,
    validation_status: valid ? 'valid' : 'invalid',
    is_locked: false,
    created_at: '2026-08-31T10:00:00Z',
    validation_report: datasetValidation(valid, !valid),
  };
}

// ---------------- T-18: config catalog (New Evaluation wizard) ----------------

const PROFILE_METRICS = [
  { name: 'context_recall', enabled: true, threshold: null, weight: 1.0 },
  { name: 'context_precision', enabled: true, threshold: null, weight: 1.0 },
  { name: 'faithfulness', enabled: true, threshold: null, weight: 1.0 },
  { name: 'answer_relevancy', enabled: true, threshold: null, weight: 1.0 },
  { name: 'entity_consistency', enabled: true, threshold: null, weight: 1.0 },
  { name: 'temporal_consistency', enabled: true, threshold: null, weight: 1.0 },
  { name: 'numerical_consistency', enabled: true, threshold: 0.9, weight: 1.0 },
];

export function profileList(total = 1, ragUrl: string | null = null): ProfileListResponse {
  return {
    items: Array.from({ length: total }, (_, i) => ({
      name: i === 0 ? 'default' : `profile-${i}`,
      version: 'v1',
      domain: 'general',
      metrics: PROFILE_METRICS,
      severity_mapping: { numerical_mismatch: 'CRITICAL' },
      quality_gate: null,
      rag_input: { mode: ragUrl ? 'http' : 'golden_replay', url: ragUrl, timeout: 30, retry: 2 },
      judge: {
        provider: 'openai', model: 'test-judge', model_version: 'v1',
        temperature: 0, max_tokens: 1024, timeout: 30, retry: 3,
      },
      pipeline: null,
    })),
    total,
  };
}

export function metricList(total = 7): MetricListResponse {
  const base = [
    {
      name: 'context_recall', category: 'retrieval', engine: 'ragas', version: 'ragas-0.4.3',
      description: '上下文召回', input_requirements: ['question', 'contexts', 'reference_answer'],
      direction: 'higher_is_better' as const, default_severity: null,
    },
    {
      name: 'context_precision', category: 'retrieval', engine: 'ragas', version: 'ragas-0.4.3',
      description: '上下文精确率', input_requirements: ['question', 'contexts'],
      direction: 'higher_is_better' as const, default_severity: null,
    },
    {
      name: 'faithfulness', category: 'generation', engine: 'ragas', version: 'ragas-0.4.3',
      description: '忠实度', input_requirements: ['question', 'answer', 'contexts'],
      direction: 'higher_is_better' as const, default_severity: null,
    },
    {
      name: 'answer_relevancy', category: 'generation', engine: 'ragas', version: 'ragas-0.4.3',
      description: '答案相关性', input_requirements: ['question', 'answer'],
      direction: 'higher_is_better' as const, default_severity: null,
    },
    {
      name: 'entity_consistency', category: 'integrity', engine: 'integrity', version: 'integrity-normalization-v1',
      description: '实体一致性', input_requirements: ['answer', 'reference_answer'],
      direction: 'higher_is_better' as const, default_severity: 'ERROR',
    },
    {
      name: 'temporal_consistency', category: 'integrity', engine: 'integrity', version: 'integrity-normalization-v1',
      description: '时序一致性', input_requirements: ['answer', 'reference_answer'],
      direction: 'higher_is_better' as const, default_severity: 'ERROR',
    },
    {
      name: 'numerical_consistency', category: 'integrity', engine: 'integrity', version: 'integrity-normalization-v1',
      description: '数值一致性', input_requirements: ['answer', 'reference_answer'],
      direction: 'higher_is_better' as const, default_severity: 'CRITICAL',
    },
  ];
  return { items: base.slice(0, total), total };
}

export function saveConfigResult(created = true): SaveConfigResponse {
  return {
    config_id: 'cfg-1111',
    config_version: 'abc123',
    name: 'default',
    domain: 'general',
    profile: 'default',
    created,
  };
}

export function createRunResult(): CreateRunResponse {
  return {
    run_id: 'run-aaaa-1111',
    status: 'pending',
    reproducibility_meta: {},
  };
}
