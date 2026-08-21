import React from 'react'
import { useApiClient } from '../lib/api'

interface QualifiedLead {
  id: number
  lead_id: number
  niche: string | null
  business_name: string | null
  phone: string | null
  reply_text: string | null
  qualification_reason: string | null
  accepted_at: string
  notification_status: string
}

function QualifiedLeads() {
  const [leads, setLeads] = React.useState<QualifiedLead[]>([])
  const [loading, setLoading] = React.useState(true)
  const api = useApiClient()

  React.useEffect(() => {
    async function fetchLeads() {
      try {
        const resp = await api.get('/api/qualified-leads')
        const data = await resp.json()
        setLeads(Array.isArray(data) ? data : [])
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    fetchLeads()
  }, [api])

  if (loading) {
    return <div className="p-8">Loading qualified leads...</div>
  }

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Qualified Leads</h1>
      <div className="bg-white p-4 rounded-lg shadow mb-4">
        <table className="w-full">
          <thead>
            <tr>
              <th className="p-2 border text-left">Lead</th>
              <th className="p-2 border text-left">Business</th>
              <th className="p-2 border text-left">Niche</th>
              <th className="p-2 border text-left">Phone</th>
              <th className="p-2 border text-left">Reply</th>
              <th className="p-2 border text-left">Accepted At</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => (
              <tr key={lead.id} className="border-b">
                <td className="p-2">{lead.lead_id}</td>
                <td className="p-2">{lead.business_name || '-'}</td>
                <td className="p-2">{lead.niche || '-'}</td>
                <td className="p-2">{lead.phone || '-'}</td>
                <td className="p-2">{lead.reply_text || '-'}</td>
                <td className="p-2">{lead.accepted_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default QualifiedLeads