import { createBrowserRouter } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Leads from './pages/Leads'
import QualifiedLeads from './pages/QualifiedLeads'
import Campaigns from './pages/Campaigns'
import Approvals from './pages/Approvals'
import MessageQueue from './pages/MessageQueue'
import FollowUps from './pages/FollowUps'
import Replies from './pages/Replies'
import Analytics from './pages/Analytics'
import Activity from './pages/Activity'
import Settings from './pages/Settings'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'dashboard', element: <Dashboard /> },
      { path: 'leads', element: <Leads /> },
      { path: 'qualified-leads', element: <QualifiedLeads /> },
      { path: 'campaigns', element: <Campaigns /> },
      { path: 'approvals', element: <Approvals /> },
      { path: 'message-queue', element: <MessageQueue /> },
      { path: 'follow-ups', element: <FollowUps /> },
      { path: 'replies', element: <Replies /> },
      { path: 'analytics', element: <Analytics /> },
      { path: 'activity', element: <Activity /> },
      { path: 'settings', element: <Settings /> },
    ],
  },
])