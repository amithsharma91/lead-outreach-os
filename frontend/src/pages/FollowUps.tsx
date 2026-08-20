import React from 'react'

interface Overview {
  template: string
  pending_drafts: number
}

interface RunReport {
  created: number
  skipped: {
    replied: number
    stopped: number
    max_reached: number
    not_due: number
  }
  total_candidates: number
}

function FollowUps() {
  const [overview, setOverview] = React.useState<Overview | null>(null)
  const [report, setReport] = React.useState<RunReport | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [running, setRunning] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  async function load() {
    try {
      const resp = await fetch('/api/follow-ups/overview')
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      setOverview(await resp.json())
    } catch (e) {
      console.error(e)
      setError('Failed to load follow-up overview')
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    load()
  }, [])

  async function run() {
    setRunning(true)
    setError(null)
    try {
      const resp = await fetch('/api/follow-ups/run', { method: 'POST' })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      setReport(await resp.json())
      await load()
    } catch (e) {
      console.error(e)
      setError('Follow-up run failed')
    } finally {
      setRunning(false)
    }
  }

  if (loading) {
    return <div className="p-8">Loading follow-ups...</div>
  }

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Follow-ups</h1>
      {error && <div className="bg-red-50 text-red-700 p-3 rounded mb-4">{error}</div>}
      <div className="grid grid-cols-2 gap-4 mb-4 max-w-md">
        <div className="bg-white p-4 rounded-lg shadow">
          <h3 className="text-sm text-gray-500">Template</h3>
          <p className="text-xl font-bold">{overview?.template ?? '-'}</p>
        </div>
        <div className="bg-white p-4 rounded-lg shadow">
          <h3 className="text-sm text-gray-500">Pending Drafts</h3>
          <p className="text-xl font-bold">{overview?.pending_drafts ?? 0}</p>
        </div>
      </div>
      <div className="bg-white p-4 rounded-lg shadow mb-4 flex items-center gap-4">
        <button
          onClick={run}
          disabled={running}
          className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {running ? 'Running...' : 'Scan for Due Follow-ups'}
        </button>
        {report && (
          <span className="text-sm text-gray-600">
            created {report.created} · replied {report.skipped.replied} · stopped{' '}
            {report.skipped.stopped} · max reached {report.skipped.max_reached} · not due{' '}
            {report.skipped.not_due} · candidates {report.total_candidates}
          </span>
        )}
      </div>
      <p className="text-sm text-gray-500">
        Follow-ups are created as drafts and appear in Approvals for human sign-off before
        they can be enqueued.
      </p>
    </div>
  )
}

export default FollowUps