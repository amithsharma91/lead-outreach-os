import React from 'react'

interface AnalyticsData {
  leads: {
    total: number
    by_outreach_status: Record<string, number>
    do_not_contact: number
    by_priority: Record<string, number>
  }
  messages: {
    total: number
    by_status: Record<string, number>
    sent: number
    replied: number
    failed: number
  }
  campaigns: Array<{
    id: number
    name: string
    template_type: string
    active: boolean
    total_messages: number
    drafts: number
    sent: number
    replied: number
    failed: number
    follow_ups: number
    reply_rate: number
  }>
  replies: {
    total: number
    by_classification: Record<string, number>
    avg_confidence: number
  }
  follow_ups: {
    total: number
    created: number
    sent: number
    replied: number
    by_status: Record<string, number>
  }
  sent_today: number
  replies_today: number
}

function Analytics() {
  const [data, setData] = React.useState<AnalyticsData | null>(null)
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    async function fetchAnalytics() {
      try {
        const resp = await fetch('/api/analytics/overview')
        const payload = await resp.json()
        setData(payload)
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    fetchAnalytics()
  }, [])

  if (loading) {
    return <div className="p-8">Loading analytics...</div>
  }

  if (!data) {
    return <div className="p-8 text-gray-500">No analytics available.</div>
  }

  const overviewCards = [
    { label: 'Total Messages', value: data.messages.total },
    { label: 'Sent', value: data.messages.sent },
    { label: 'Replied', value: data.messages.replied },
    { label: 'Failed', value: data.messages.failed },
    { label: 'Replies (all)', value: data.replies.total },
    { label: 'Sent Today', value: data.sent_today },
    { label: 'Replies Today', value: data.replies_today },
    { label: 'Follow-up Messages', value: data.follow_ups.total },
  ]

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Analytics</h1>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        {overviewCards.map((card) => (
          <div key={card.label} className="bg-white p-4 rounded-lg shadow">
            <h3 className="text-sm text-gray-500">{card.label}</h3>
            <p className="text-2xl font-bold">{card.value}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        <div className="bg-white p-4 rounded-lg shadow">
          <h2 className="text-lg font-semibold mb-3">Message Funnel</h2>
          <table className="w-full">
            <tbody>
              {Object.entries(data.messages.by_status).map(([status, count]) => (
                <tr key={status} className="border-b">
                  <td className="p-2">{status}</td>
                  <td className="p-2 text-right font-semibold">{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="bg-white p-4 rounded-lg shadow">
          <h2 className="text-lg font-semibold mb-3">Reply Classifications</h2>
          <table className="w-full">
            <tbody>
              {Object.entries(data.replies.by_classification).map(
                ([classification, count]) => (
                  <tr key={classification} className="border-b">
                    <td className="p-2">{classification}</td>
                    <td className="p-2 text-right font-semibold">{count}</td>
                  </tr>
                )
              )}
              <tr className="border-b">
                <td className="p-2 text-gray-500">Avg confidence</td>
                <td className="p-2 text-right">
                  {data.replies.avg_confidence.toFixed(4)}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="bg-white p-4 rounded-lg shadow mb-6">
        <h2 className="text-lg font-semibold mb-3">Campaign Performance</h2>
        <table className="w-full">
          <thead>
            <tr>
              <th className="p-2 border text-left">Campaign</th>
              <th className="p-2 border text-left">Total</th>
              <th className="p-2 border text-left">Sent</th>
              <th className="p-2 border text-left">Replied</th>
              <th className="p-2 border text-left">Failed</th>
              <th className="p-2 border text-left">Follow-ups</th>
              <th className="p-2 border text-left">Reply Rate</th>
            </tr>
          </thead>
          <tbody>
            {data.campaigns.map((campaign) => (
              <tr key={campaign.id} className="border-b">
                <td className="p-2">{campaign.name}</td>
                <td className="p-2">{campaign.total_messages}</td>
                <td className="p-2">{campaign.sent}</td>
                <td className="p-2">{campaign.replied}</td>
                <td className="p-2">{campaign.failed}</td>
                <td className="p-2">{campaign.follow_ups}</td>
                <td className="p-2">{(campaign.reply_rate * 100).toFixed(1)}%</td>
              </tr>
            ))}
            {data.campaigns.length === 0 && (
              <tr>
                <td className="p-2 text-gray-500" colSpan={7}>
                  No campaigns yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white p-4 rounded-lg shadow">
          <h2 className="text-lg font-semibold mb-3">Lead Outreach Status</h2>
          <table className="w-full">
            <tbody>
              {Object.entries(data.leads.by_outreach_status).map(([status, count]) => (
                <tr key={status} className="border-b">
                  <td className="p-2">{status}</td>
                  <td className="p-2 text-right font-semibold">{count}</td>
                </tr>
              ))}
              <tr className="border-b">
                <td className="p-2 text-gray-500">Do Not Contact</td>
                <td className="p-2 text-right">{data.leads.do_not_contact}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="bg-white p-4 rounded-lg shadow">
          <h2 className="text-lg font-semibold mb-3">Follow-up Engagement</h2>
          <table className="w-full">
            <tbody>
              <tr className="border-b">
                <td className="p-2">Total follow-ups</td>
                <td className="p-2 text-right font-semibold">{data.follow_ups.total}</td>
              </tr>
              <tr className="border-b">
                <td className="p-2">Created (drafts)</td>
                <td className="p-2 text-right">{data.follow_ups.created}</td>
              </tr>
              <tr className="border-b">
                <td className="p-2">Sent</td>
                <td className="p-2 text-right">{data.follow_ups.sent}</td>
              </tr>
              <tr className="border-b">
                <td className="p-2">Replied</td>
                <td className="p-2 text-right">{data.follow_ups.replied}</td>
              </tr>
              {Object.entries(data.follow_ups.by_status).map(([status, count]) => (
                <tr key={status} className="border-b">
                  <td className="p-2 text-gray-500">{status}</td>
                  <td className="p-2 text-right">{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

export default Analytics