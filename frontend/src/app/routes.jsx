import { NAV_ITEMS } from './navigation.js';
import { ModulePlaceholderPage } from '../pages/ModulePlaceholderPage.jsx';
import { NotFoundPage } from '../pages/NotFoundPage.jsx';
import { OverviewPage } from '../pages/OverviewPage.jsx';
import { SystemStatusPage } from '../pages/SystemStatusPage.jsx';
import { AccountPage } from '../pages/account/AccountPage.jsx';
import { MagicLinkPage, MagicLinkRequestPage } from '../pages/auth/MagicLinkPage.jsx';
import { ForgotPasswordPage } from '../pages/auth/ForgotPasswordPage.jsx';
import { LoginPage } from '../pages/auth/LoginPage.jsx';
import { RegisterPage } from '../pages/auth/RegisterPage.jsx';
import { ResetPasswordPage } from '../pages/auth/ResetPasswordPage.jsx';
import { VerifyEmailPage } from '../pages/auth/VerifyEmailPage.jsx';
import { OwnerPage } from '../pages/owner/OwnerPage.jsx';
import { AnalyticsPage } from '../pages/analytics/AnalyticsPage.jsx';
import { ConversationsPage } from '../pages/conversations/ConversationsPage.jsx';
import { AnalyticsWorkspacePage } from '../pages/analytics/AnalyticsWorkspacePage.jsx';
import { ComparePage } from '../pages/predictive/ComparePage.jsx';
import { ModelPage } from '../pages/predictive/ModelPage.jsx';
import { ForecastPage } from '../pages/predictive/ForecastPage.jsx';
import { ModelsPage } from '../pages/predictive/ModelsPage.jsx';
import { PredictionsPage } from '../pages/predictive/PredictionsPage.jsx';
import { PredictionWorkspacePage } from '../pages/predictive/PredictionWorkspacePage.jsx';
import { BundlePage } from '../pages/research/BundlePage.jsx';
import { EventPage } from '../pages/research/EventPage.jsx';
import { InfluencePage } from '../pages/research/InfluencePage.jsx';
import { NewResearchPage } from '../pages/research/NewResearchPage.jsx';
import { ProjectResearchPage } from '../pages/research/ProjectResearchPage.jsx';
import { SessionPage } from '../pages/research/SessionPage.jsx';
import { SettingsPage as ResearchSettingsPage } from '../pages/research/SettingsPage.jsx';
import { DashboardPage } from '../pages/phase7/DashboardPage.jsx';
import { ComputePage } from '../pages/dev/ComputePage.jsx';
import { DevProjectPage } from '../pages/dev/DevProjectPage.jsx';
import { DevelopmentPage } from '../pages/dev/DevelopmentPage.jsx';
import { ExamPage, ExamSubjectPage } from '../pages/phase7/ExamPage.jsx';
import { IntelligencePage } from '../pages/phase7/IntelligencePage.jsx';
import { RegistryModelPage, RegistryPage } from '../pages/phase7/RegistryPage.jsx';
import { ReportPage } from '../pages/phase7/ReportPage.jsx';
import { ReportsPage } from '../pages/phase7/ReportsPage.jsx';
import { SchedulePage, SchedulesPage } from '../pages/phase7/SchedulesPage.jsx';
import { WorkspacePage, WorkspacesPage } from '../pages/phase7/WorkspacesPage.jsx';
import { DiscoverPage } from '../modules/research_intelligence/pages/DiscoverPage.jsx';
import { LibraryPage as RiLibraryPage } from '../modules/research_intelligence/pages/LibraryPage.jsx';
import { AssistantChooserPage, StudioChooserPage, ReportsIndexPage } from '../modules/research_intelligence/pages/ChooserPages.jsx';
import { ResearchSettingsPage as RiSettingsPage } from '../modules/research_intelligence/pages/ResearchSettingsPage.jsx';
import { StudioPage } from '../modules/research_intelligence/pages/StudioPage.jsx';
import { ReportPage as RiReportPage } from '../modules/research_intelligence/pages/ReportPage.jsx';
import { NotebookListPage } from '../modules/notebook/pages/NotebookListPage.jsx';
import { NotebookPage } from '../modules/notebook/pages/NotebookPage.jsx';
import { AssetPage } from '../pages/projects/AssetPage.jsx';
import { ProjectPage } from '../pages/projects/ProjectPage.jsx';
import { ProjectsPage } from '../pages/projects/ProjectsPage.jsx';

// Pages that exist so far. Every other navigation entry gets the placeholder.
const IMPLEMENTED = {
  overview: <OverviewPage />,
  status: <SystemStatusPage />,
  account: <AccountPage />,
  owner: <OwnerPage />,
  projects: <ProjectsPage />,
  uploads: <ProjectsPage pickerFor="uploads" />,
  analytics: <AnalyticsPage />,
  conversations: <ConversationsPage />,
  predictions: <PredictionsPage />,
  notebook: <NotebookListPage />,
  dashboard: <DashboardPage />,
  exam: <ExamPage />,
  reports: <ReportsPage />,
  schedules: <SchedulesPage />,
  workspaces: <WorkspacesPage />,
  intelligence: <IntelligencePage />,
  models: <RegistryPage />,
  development: <DevelopmentPage />,
  compute: <ComputePage />,
};

const appRoutes = NAV_ITEMS.map((item) => ({
  path: item.path,
  title: item.label,
  access: item.access,
  element: IMPLEMENTED[item.id] ?? <ModulePlaceholderPage item={item} />,
}));

// Pages reached from inside a module (not in the sidebar).
const nestedRoutes = [
  { path: '/notebook/:notebookId', title: 'Notebook', access: 'user', element: <NotebookPage /> },
  // The two older research areas now open Notebook. Their other pages stay reachable by address so nothing saved there becomes unviewable.
  { path: '/research', title: 'Notebook', access: 'user', element: <NotebookListPage /> },
  { path: '/research-intelligence', title: 'Notebook', access: 'user', element: <NotebookListPage /> },
  // Research Intelligence: the eight sections are fixed paths and must come before the parameterised workspace routes.
  { path: '/research-intelligence/workspaces', title: 'Notebook', access: 'user', element: <NotebookListPage /> },
  { path: '/research-intelligence/discover', title: 'Discover research', access: 'user', element: <DiscoverPage /> },
  { path: '/research-intelligence/library', title: 'Source library', access: 'user', element: <RiLibraryPage /> },
  { path: '/research-intelligence/assistant', title: 'Research assistant', access: 'user', element: <AssistantChooserPage /> },
  { path: '/research-intelligence/studio', title: 'Research studio', access: 'user', element: <StudioChooserPage /> },
  { path: '/research-intelligence/reports', title: 'Research reports', access: 'user', element: <ReportsIndexPage /> },
  { path: '/research-intelligence/settings', title: 'Research settings', access: 'user', element: <RiSettingsPage /> },
  { path: '/research-intelligence/:workspaceId/studio', title: 'Research studio', access: 'user', element: <StudioPage /> },
  { path: '/research-intelligence/:workspaceId/reports/:reportId', title: 'Research report', access: 'user', element: <RiReportPage /> },
  { path: '/research-intelligence/:workspaceId', title: 'Notebook', access: 'user', element: <NotebookPage /> },
  { path: '/projects/:projectId', title: 'Project', access: 'user', element: <ProjectPage /> },
  { path: '/conversations/:kind/:conversationId', title: 'Conversation', access: 'user', element: <ConversationsPage /> },
  { path: '/analytics/:assetId', title: 'Analyse dataset', access: 'user', element: <AnalyticsWorkspacePage /> },
  // Predictions: fixed paths first, then the ones with a parameter.
  { path: '/predictions/forecast', title: 'Forecast', access: 'user', element: <ForecastPage /> },
  { path: '/predictions/models', title: 'Models', access: 'user', element: <ModelsPage /> },
  { path: '/predictions/compare', title: 'Compare models', access: 'user', element: <ComparePage /> },
  { path: '/predictions/models/:modelId', title: 'Model', access: 'user', element: <ModelPage /> },
  { path: '/predictions/data/:assetId', title: 'Predict from dataset', access: 'user', element: <PredictionWorkspacePage /> },
  // Research: fixed paths first, then the ones with a parameter.
  { path: '/research/settings', title: 'Research settings', access: 'user', element: <ResearchSettingsPage /> },
  { path: '/research/p/:projectId/new', title: 'New research', access: 'user', element: <NewResearchPage /> },
  { path: '/research/p/:projectId', title: 'Project research', access: 'user', element: <ProjectResearchPage /> },
  { path: '/research/session/:sessionId', title: 'Research', access: 'user', element: <SessionPage /> },
  { path: '/research/bundle/:bundleId', title: 'Research result', access: 'user', element: <BundlePage /> },
  { path: '/research/news/event/:eventId', title: 'Event', access: 'user', element: <EventPage /> },
  { path: '/research/influence/:analysisId', title: 'Influence analysis', access: 'user', element: <InfluencePage /> },
  // Phase 7
  { path: '/models/:modelId', title: 'Registered model', access: 'user', element: <RegistryModelPage /> },
  { path: '/reports/:reportId', title: 'Report', access: 'user', element: <ReportPage /> },
  { path: '/exam/:subjectId', title: 'Exam subject', access: 'user', element: <ExamSubjectPage /> },
  { path: '/schedules/:scheduleId', title: 'Schedule', access: 'user', element: <SchedulePage /> },
  { path: '/workspaces/:workspaceId', title: 'Workspace', access: 'user', element: <WorkspacePage /> },
  { path: '/development/:projectId', title: 'Project workspace', access: 'special', element: <DevProjectPage /> },
  { path: '/projects/:projectId/assets/:assetId', title: 'File', access: 'user', element: <AssetPage /> },
];

// Signed-out screens use their own frame (layout: 'auth'). guestOnly pages send signed-in people home.
const authRoutes = [
  { path: '/login', title: 'Sign in', access: 'public', layout: 'auth', element: <LoginPage /> },
  { path: '/register', title: 'Create account', access: 'public', layout: 'auth', guestOnly: true, element: <RegisterPage /> },
  { path: '/forgot-password', title: 'Reset password', access: 'public', layout: 'auth', guestOnly: true, element: <ForgotPasswordPage /> },
  { path: '/reset-password', title: 'Choose a new password', access: 'public', layout: 'auth', element: <ResetPasswordPage /> },
  { path: '/magic-link', title: 'Sign in with a link', access: 'public', layout: 'auth', element: <MagicLinkPage /> },
  { path: '/magic-link/request', title: 'Email me a sign-in link', access: 'public', layout: 'auth', guestOnly: true, element: <MagicLinkRequestPage /> },
  { path: '/verify-email', title: 'Confirm email', access: 'public', layout: 'auth', element: <VerifyEmailPage /> },
];

export const routes = [...appRoutes, ...nestedRoutes, ...authRoutes];
export const notFound = <NotFoundPage />;
