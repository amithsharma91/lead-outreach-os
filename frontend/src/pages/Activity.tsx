import React from 'react'
import { useApiClient } from '../lib/api'

interface ActivityEvent {
  id: number
  lead_id: number | null
  event_type: string
  event_data: string | null
  timestamp: string
}

function Activity() {
  const [events, setEvents] = React.useState<ActivityEvent[]>([])
  const [loading, setLoading] = React.useState(true)
  const api = useApiClient()

  React.useEffect(() => {
    async function fetchEvents() {
      try {
        const resp = await api.get('/api/activity')
        const data = await resp.json()
        setEvents(Array.isArray(data) ? data : [])
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    fetchEvents()
  }, [api])

  if (loading) {
    return <div className="p-8">Loading activity...</div>
  }

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Activity Log</h1>
      <div className="bg-white p-4 rounded-lg shadow mb-4">
        <table className="w-full">
          <thead>
            <tr>
              <th className="p-2 border text-left">Event</th>
              <th className="p-2 border text-left">Lead</th>
              <th className="p-2 border text-left">Data</th>
              <th className="p-2 border text-left">Timestamp</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id} className="border-b">
                <td className="p-2">{event.event_type}</td>
                <td className="p-2">{event.lead_id ?? '-'}</td>
                <td className="p-2">{event.event_data || '-'}</td>
                <td className="p-2">{event.timestamp}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default Activity