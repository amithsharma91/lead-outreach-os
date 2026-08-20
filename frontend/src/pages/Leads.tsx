import React from 'react'

interface Lead {
  lead_id: string
  business_name: string
  niche: string | null
  city: string | null
  website: string | null
  phone: string | null
  qualification_status: string
  outreach_status: string
}

function Leads() {
  const [leads, setLeads] = React.useState<Lead[]>([])
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    async function fetchLeads() {
      try {
        const resp = await fetch('/api/leads')
        const data = await resp.json()
        setLeads(data.leads || [])
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    fetchLeads()
  }, [])

  if (loading) {
    return <div className="p-8">Loading leads...</div>
  }

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <h1 className="text-3xl font-bold mb-6">Leads</h1>
      <div className="bg-white p-4 rounded-lg shadow mb-4">
        <table className="w-full">
          <thead>
            <tr>
              <th className="p-2 border text-left">Lead ID</th>
              <th className="p-2 border text-left">Business</th>
              <th className="p-2 border text-left">Niche</th>
              <th className="p-2 border text-left">City</th>
              <th className="p-2 border text-left">Website</th>
              <th className="p-2 border text-left">Phone</th>
              <th className="p-2 border text-left">Qualification</th>
              <th className="p-2 border text-left">Status</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => (
              <tr key={lead.lead_id} className="border-b">
                <td className="p-2">{lead.lead_id}</td>
                <td className="p-2">{lead.business_name}</td>
                <td className="p-2">{lead.niche || '-'}</td>
                <td className="p-2">{lead.city || '-'}</td>
                <td className="p-2">{lead.website || '-'}</td>
                <td className="p-2">{lead.phone || '-'}</td>
                <td className="p-2">{lead.qualification_status}</td>
                <td className="p-2">{lead.outreach_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default Leads