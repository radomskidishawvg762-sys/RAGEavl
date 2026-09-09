import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';

const ComparePage = lazy(() => import('./pages/ComparePage').then((m) => ({ default: m.ComparePage })));
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })));
const DatasetDetailPage = lazy(() => import('./pages/DatasetDetailPage').then((m) => ({ default: m.DatasetDetailPage })));
const DatasetImportPage = lazy(() => import('./pages/DatasetImportPage').then((m) => ({ default: m.DatasetImportPage })));
const DatasetsPage = lazy(() => import('./pages/DatasetsPage').then((m) => ({ default: m.DatasetsPage })));
const EvaluationDetailPage = lazy(() => import('./pages/EvaluationDetailPage').then((m) => ({ default: m.EvaluationDetailPage })));
const EvaluationListPage = lazy(() => import('./pages/EvaluationListPage').then((m) => ({ default: m.EvaluationListPage })));
const FailureExplorerPage = lazy(() => import('./pages/FailureExplorerPage').then((m) => ({ default: m.FailureExplorerPage })));
const MetricsPage = lazy(() => import('./pages/MetricsPage').then((m) => ({ default: m.MetricsPage })));
const NewEvaluationPage = lazy(() => import('./pages/NewEvaluationPage').then((m) => ({ default: m.NewEvaluationPage })));
const ProfilesPage = lazy(() => import('./pages/ProfilesPage').then((m) => ({ default: m.ProfilesPage })));
const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then((m) => ({ default: m.ProjectsPage })));
const QualityGatePage = lazy(() => import('./pages/QualityGatePage').then((m) => ({ default: m.QualityGatePage })));
const RegressionPage = lazy(() => import('./pages/RegressionPage').then((m) => ({ default: m.RegressionPage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })));
import { AppShell } from './layout/AppShell';
import { EmptyState } from './components/primitives/Feedback';

export default function App() {
  return (
    <Suspense fallback={<div style={{ padding: 'var(--sp-5)', color: 'var(--color-text-muted)' }}>正在加载页面…</div>}>
      <Routes>
        <Route element={<AppShell />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/evaluations" element={<EvaluationListPage />} />
        <Route path="/evaluations/new" element={<NewEvaluationPage />} />
        <Route path="/evaluations/:runId" element={<EvaluationDetailPage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/regression" element={<RegressionPage />} />
        <Route path="/quality-gate" element={<QualityGatePage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/datasets/import" element={<DatasetImportPage />} />
        <Route path="/datasets/:datasetId" element={<DatasetDetailPage />} />
        <Route path="/failures" element={<FailureExplorerPage />} />
        <Route path="/profiles" element={<ProfilesPage />} />
        <Route path="/metrics" element={<MetricsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route
          path="*"
          element={
            <EmptyState
              title="页面不存在"
              description="请使用左侧导航进入对应功能。"
              icon="question"
            />
          }
        />
        </Route>
      </Routes>
    </Suspense>
  );
}
