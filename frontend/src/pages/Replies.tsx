import React from 'react'
import { useApiClient } from '../lib/api'

interface Reply {
  id: number
  lead_id: number
  message_id: number | null
  channel: string
  reply_text: string
  classification: string
  confidence: number | null
  received_at: string
}

function Replies() {
  const [replies, setReplies] = React.useState<Reply[]>([])
  const [loading, setLoading] = React.useState(true)
  const api = useApiClient()

  React.useEffect(() => {
    async function fetchReplies() {
      try {
        const resp = await api.get('/api/replies')
        const data = await resp.json()
        setReplies(Array.isArray(data) ? data : [])
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    fetchReplies()
  }, [api])

  if (loading) {
    return <div className="p-8">Loading replies...</div>
  }

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Replies</h1>
      <div className="bg-white p-4 rounded-lg shadow mb-4">
        <table className="w-full">
          <thead>
            <tr>
              <th className="p-2 border text-left">Lead</th>
              <th className="p-2 border text-left">Reply</th>
              <th className="p-2 border text-left">Classification</th>
              <th className="p-2 border text-left">Confidence</th>
              <th className="p-2 border text-left">Received At</th>
            </tr>
          </thead>
          <tbody>
            {replies.map((reply) => (
              <tr key={reply.id} className="border-b">
                <td className="p-2">{reply.lead_id}</td>
                <td className="p-2">{reply.reply_text || '-'}</td>
                <td className="p-2">{reply.classification}</td>
                <td className="p-2">{reply.confidence !== null ? reply.confidence.toFixed(2) : '-'}</td>
                <td className="p-2">{reply.received_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default Replies