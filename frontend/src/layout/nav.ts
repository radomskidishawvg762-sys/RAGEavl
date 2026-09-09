/**
 * Navigation model (Phase 2 产品闭环 IA §三). Project 是一级上下文，
 * Project Selector 常驻 Workspace Header。所有页面均为真实实现（无占位）：
 * 数据来自后端；`gap` 不再使用 — 后端缺失的能力按诚实空态 / 声明呈现。
 */

import type { IconName } from '../status/status';

export interface NavItem {
  to: string;
  label: string;
  labelZh: string;
  labelEn: string;
  icon: IconName;
  end?: boolean;
}

export interface NavSection {
  key: string;
  label: string;
  labelZh: string;
  labelEn: string;
  items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = [
  {
    key: 'overview',
    label: 'OVERVIEW / 概览', labelZh: '概览', labelEn: 'Overview',
    items: [{ to: '/', label: 'Dashboard / 仪表盘', labelZh: '仪表盘', labelEn: 'Dashboard', icon: 'circle', end: true }],
  },
  {
    key: 'workspace',
    label: 'WORKSPACE / 工作区', labelZh: '工作区', labelEn: 'Workspace',
    items: [
      { to: '/projects', label: 'Project / 项目', labelZh: '项目', labelEn: 'Project', icon: 'spark' },
      { to: '/datasets', label: 'Datasets / 数据集', labelZh: '数据集', labelEn: 'Datasets', icon: 'circle' },
      { to: '/evaluations', label: 'Evaluations / 评估', labelZh: '评估', labelEn: 'Evaluations', icon: 'circle' },
    ],
  },
  {
    key: 'evaluation',
    label: 'EVALUATION / 评估', labelZh: '评估', labelEn: 'Evaluation',
    items: [
      { to: '/evaluations/new', label: 'New Evaluation / 新建评估', labelZh: '新建评估', labelEn: 'New Evaluation', icon: 'arrow-right' },
      { to: '/compare', label: 'Compare / 对比', labelZh: '对比', labelEn: 'Compare', icon: 'arrow-right' },
      { to: '/regression', label: 'Regression / 回归', labelZh: '回归', labelEn: 'Regression', icon: 'arrow-down' },
      { to: '/quality-gate', label: 'Quality Gate / 质量门禁', labelZh: '质量门禁', labelEn: 'Quality Gate', icon: 'alert' },
    ],
  },
  {
    key: 'diagnosis',
    label: 'DIAGNOSIS / 诊断', labelZh: '诊断', labelEn: 'Diagnosis',
    items: [{ to: '/failures', label: 'Failure Explorer / 失败浏览器', labelZh: '失败浏览器', labelEn: 'Failure Explorer', icon: 'cross' }],
  },
  {
    key: 'configuration',
    label: 'CONFIGURATION / 配置', labelZh: '配置', labelEn: 'Configuration',
    items: [
      { to: '/profiles', label: 'Configuration / 配置', labelZh: '配置', labelEn: 'Configuration', icon: 'circle' },
      { to: '/metrics', label: 'Metrics / 指标', labelZh: '指标', labelEn: 'Metrics', icon: 'circle' },
    ],
  },
  {
    key: 'system',
    label: 'SYSTEM / 系统', labelZh: '系统', labelEn: 'System',
    items: [{ to: '/settings', label: 'Settings / 设置', labelZh: '设置', labelEn: 'Settings', icon: 'info' }],
  },
];
