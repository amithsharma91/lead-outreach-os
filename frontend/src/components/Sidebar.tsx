import { NavLink } from 'react-router-dom'

const links = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/leads', label: 'Leads' },
  { to: '/qualified-leads', label: 'Qualified Leads' },
  { to: '/campaigns', label: 'Campaigns' },
  { to: '/approvals', label: 'Approvals' },
  { to: '/message-queue', label: 'Message Queue' },
  { to: '/follow-ups', label: 'Follow-ups' },
  { to: '/replies', label: 'Replies' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/activity', label: 'Activity' },
  { to: '/settings', label: 'Settings' },
]

export default function Sidebar() {
  return (
    <aside className="w-56 shrink-0 bg-gray-900 text-gray-300 flex flex-col">
      <div className="px-4 py-5 border-b border-gray-800">
        <h1 className="text-lg font-bold text-white">Lead Outreach OS</h1>
        <p className="text-xs text-gray-500 mt-1">Phase 2 console</p>
      </div>
      <nav className="flex-1 py-4">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.to === '/dashboard'}
            className={({ isActive }) =>
              `block px-4 py-2 text-sm hover:bg-gray-800 hover:text-white ${
                isActive ? 'bg-gray-800 text-white font-semibold' : ''
              }`
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}