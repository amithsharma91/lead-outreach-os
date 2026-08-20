import React from 'react'

interface Campaign {
  id: number
  name: string
  description: string | null
  template_type: string
  active: boolean
  start_time: string | null
  end_time: string | null
}

function Campaigns() {
  const [campaigns, setCampaigns] = React.useState<Campaign[]>([])
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    async function fetchCampaigns() {
      try {
        const resp = await fetch('/api/campaigns')
        const data = await resp.json()
        setCampaigns(Array.isArray(data) ? data : [])
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    fetchCampaigns()
  }, [])

  if (loading) {
    return <div className="p-8">Loading campaigns...</div>
  }

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Campaigns</h1>
      <div className="bg-white p-4 rounded-lg shadow mb-4">
        <table className="w-full">
          <thead>
            <tr>
              <th className="p-2 border text-left">Campaign Name</th>
              <th className="p-2 border text-left">Template</th>
              <th className="p-2 border text-left">Active</th>
              <th className="p-2 border text-left">Start Time</th>
              <th className="p-2 border text-left">End Time</th>
            </tr>
          </thead>
          <tbody>
            {campaigns.map((campaign) => (
              <tr key={campaign.id} className="border-b">
                <td className="p-2">{campaign.name}</td>
                <td className="p-2">{campaign.template_type || '-'}</td>
                <td className="p-2">{campaign.active ? 'Yes' : 'No'}</td>
                <td className="p-2">{campaign.start_time || '-'}</td>
                <td className="p-2">{campaign.end_time || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default Campaigns