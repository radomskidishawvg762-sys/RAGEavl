# Chinese + English UI Guidelines

## Scope

RAGEval Studio uses a same-page bilingual presentation for user-visible labels
and explanations. Chinese is the primary reading language and English is shown
adjacent to the Chinese label or on the next line. Technical identifiers remain
English and are rendered as code values, not translated prose.

The shared primitives and the primary navigation, diagnosis drawer, evidence
panel, dataset/project/metric entry points, and status badges are migrated in
this delivery. Other page-specific copy should use the same primitives as those
pages are touched; this document is the migration contract, not a claim that
every legacy string has already been rewritten.

## Components

- `BilingualLabel`: paired short labels such as `诊断结论 / Diagnosis`.
- `BilingualText`: explanatory copy on two lines, Chinese first and English second.
- `lang="zh-CN"` and `lang="en"` are applied to each language span for screen readers.
- Shared primitives should accept `ReactNode` titles so bilingual labels do not
  require page-specific markup.

## Typography and layout

- `--font-sans` starts with `Noto Sans SC`, `PingFang SC`, then system fallbacks.
- Use relaxed line height and allow wrapping on narrow screens; never force
  translated copy into a fixed-width single line.
- Technical values such as `run_id`, `config_version`, `failure_type`, and
  taxonomy codes use the existing monospace style and remain unchanged.
- CSS classes `.label-zh` / `.label-en` are represented by the component's
  language-aware `.zh` / `.en` spans.

## Copy examples

| Surface | Chinese | English | Code |
|---|---|---|---|
| Evaluation Detail | 评估编号 | Evaluation ID | `run_id` |
| Diagnosis Card | 诊断结论：缺少参考证据 | Diagnosis: Missing Evidence | `failure_type=retrieval.missing_evidence` |
| Report Summary | 总体评分：0.85（通过） | Overall Score: 0.85 (Pass) | `overall_score` |
| Recommendation | 扩展知识库覆盖范围并复查检索策略 | Expand knowledge coverage and review retrieval strategy | `recommendation_text` |

## Testing

`frontend/tests/bilingual.test.tsx` verifies paired navigation labels and
`lang` attributes. Reviewers should additionally inspect all core pages at
desktop and mobile widths and run a screen-reader pass. Technical codes may be
visible in detail views, but they must remain English and must not be translated
or used as user-facing explanatory text.
