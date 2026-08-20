import React from 'react'

interface DashboardStats {
  total_leads: number
  qualified_leads: number
  pending: number
  contacted: number
  replies: number
  positive_replies: number
  do_not_contact: number
}

function Dashboard() {
  const [stats, setStats] = React.useState<DashboardStats | null>(null)
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    async function fetchStats() {
      try {
        const resp = await fetch('/api/dashboard')
        const data = await resp.json()
        setStats(data)
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    fetchStats()
  }, [])

  if (loading) {
    return <div className="p-8">Loading dashboard...</div>
  }

  const cards = [
    { label: 'Total Leads', value: stats?.total_leads ?? 0 },
    { label: 'Qualified Leads', value: stats?.qualified_leads ?? 0 },
    { label: 'Pending Qualification', value: stats?.pending ?? 0 },
    { label: 'Contacted', value: stats?.contacted ?? 0 },
    { label: 'Replies', value: stats?.replies ?? 0 },
    { label: 'Positive Replies', value: stats?.positive_replies ?? 0 },
    { label: 'Do Not Contact', value: stats?.do_not_contact ?? 0 },
  ]

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Dashboard</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map((card) => (
          <div key={card.label} className="bg-white p-4 rounded-lg shadow">
            <h3 className="text-sm text-gray-500">{card.label}</h3>
            <p className="text-2xl font-bold">{card.value}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

export default Dashboard