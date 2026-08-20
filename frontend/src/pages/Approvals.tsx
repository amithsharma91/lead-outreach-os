import React from 'react'

interface PendingMessage {
  id: number
  lead_id: number
  campaign_id: number | null
  channel: string
  template_type: string
  generated_message: string | null
  edited_message: string | null
  status: string
  message_sequence: number
  rejection_reason: string | null
}

interface PendingResponse {
  count: number
  messages: PendingMessage[]
}

function Approvals() {
  const [data, setData] = React.useState<PendingResponse>({ count: 0, messages: [] })
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  async function load() {
    try {
      const resp = await fetch('/api/messages/pending-approval')
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      setData(await resp.json())
    } catch (e) {
      console.error(e)
      setError('Failed to load pending approvals')
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    load()
  }, [])

  async function act(messageId: number, path: string, body: object) {
    setError(null)
    try {
      const resp = await fetch(`/api/messages/${messageId}/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!resp.ok) {
        const detail = await resp.json().catch(() => null)
        throw new Error(detail?.detail || `HTTP ${resp.status}`)
      }
      await load()
    } catch (e) {
      console.error(e)
      setError(e instanceof Error ? e.message : 'Action failed')
    }
  }

  function approve(message: PendingMessage) {
    const approvedBy = window.prompt('Approved by (name):', 'operator')
    if (approvedBy && approvedBy.trim()) {
      act(message.id, 'approve', { approved_by: approvedBy.trim() })
    }
  }

  function reject(message: PendingMessage) {
    const reason = window.prompt('Rejection reason (required):')
    if (reason && reason.trim()) {
      act(message.id, 'reject', { rejection_reason: reason.trim() })
    }
  }

  function edit(message: PendingMessage) {
    const edited = window.prompt(
      'Edit message content (forces re-approval):',
      message.generated_message ?? ''
    )
    if (edited && edited.trim()) {
      act(message.id, 'edit', { edited_message: edited.trim() })
    }
  }

  if (loading) {
    return <div className="p-8">Loading approvals...</div>
  }

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Approvals</h1>
      {error && <div className="bg-red-50 text-red-700 p-3 rounded mb-4">{error}</div>}
      <p className="mb-4 text-sm text-gray-600">
        {data.count} message{data.count === 1 ? '' : 's'} awaiting human approval. Nothing
        can be sent until approved.
      </p>
      <div className="space-y-4">
        {data.messages.map((message) => (
          <div key={message.id} className="bg-white p-4 rounded-lg shadow">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm text-gray-500">
                  Message #{message.id} · Lead #{message.lead_id} · {message.template_type}
                  {message.message_sequence > 1 ? ` · sequence ${message.message_sequence}` : ''}
                </p>
                <p className="mt-2 whitespace-pre-wrap">
                  {message.edited_message || message.generated_message || '-'}
                </p>
              </div>
              <div className="flex gap-2 shrink-0">
                <button
                  onClick={() => approve(message)}
                  className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700"
                >
                  Approve
                </button>
                <button
                  onClick={() => edit(message)}
                  className="bg-yellow-600 text-white px-4 py-2 rounded hover:bg-yellow-700"
                >
                  Edit
                </button>
                <button
                  onClick={() => reject(message)}
                  className="bg-red-600 text-white px-4 py-2 rounded hover:bg-red-700"
                >
                  Reject
                </button>
              </div>
            </div>
          </div>
        ))}
        {data.messages.length === 0 && (
          <div className="bg-white p-6 rounded-lg shadow text-gray-500">
            No messages awaiting approval.
          </div>
        )}
      </div>
    </div>
  )
}

export default Approvals