import React from 'react'

interface QueueCounts {
  queued: number
  sending: number
  retry_pending: number
  failed: number
  sent_today: number
}

interface TickReport {
  configured: boolean
  window: boolean
  daily_limit: number
  sent: number
  failed: number
  retried: number
  skipped: number
  note: string | null
}

function MessageQueue() {
  const [counts, setCounts] = React.useState<QueueCounts | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [tick, setTick] = React.useState<TickReport | null>(null)

  async function load() {
    try {
      const resp = await fetch('/api/queue/overview')
      const data = await resp.json()
      setCounts(data.counts)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    load()
  }, [])

  async function runTick() {
    try {
      const resp = await fetch('/api/queue/tick', { method: 'POST' })
      const data = await resp.json()
      setTick(data)
      await load()
    } catch (e) {
      console.error(e)
    }
  }

  if (loading) {
    return <div className="p-8">Loading message queue...</div>
  }

  const cards = [
    { label: 'Queued', value: counts?.queued ?? 0 },
    { label: 'Sending', value: counts?.sending ?? 0 },
    { label: 'Retry Pending', value: counts?.retry_pending ?? 0 },
    { label: 'Failed', value: counts?.failed ?? 0 },
    { label: 'Sent Today', value: counts?.sent_today ?? 0 },
  ]

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Message Queue</h1>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">
        {cards.map((card) => (
          <div key={card.label} className="bg-white p-4 rounded-lg shadow">
            <h3 className="text-sm text-gray-500">{card.label}</h3>
            <p className="text-2xl font-bold">{card.value}</p>
          </div>
        ))}
      </div>
      <div className="bg-white p-4 rounded-lg shadow mb-4 flex items-center gap-4">
        <button
          onClick={runTick}
          className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
        >
          Run Tick
        </button>
        {tick && (
          <span className="text-sm text-gray-600">
            sent {tick.sent} · failed {tick.failed} · retried {tick.retried} · skipped {tick.skipped}
            {tick.note ? ` (${tick.note})` : ''}
          </span>
        )}
      </div>
      <p className="text-sm text-gray-500">
        With the default configuration (provider none, daily limit 0) ticks perform zero sends.
      </p>
    </div>
  )
}

export default MessageQueue