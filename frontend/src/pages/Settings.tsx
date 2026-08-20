import React from 'react'

function Settings() {
  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <h1 className="text-3xl font-bold mb-6">Settings</h1>
      <div className="bg-white p-4 rounded-lg shadow">
        <div className="mb-4">
          <h3 className="text-sm font-medium mb-2">Database</h3>
          <p className="text-sm text-gray-500">Path: C:\tmp\lead-outreach-os\data\lead_outreach.db</p>
        </div>
        <div className="mb-4">
          <h3 className="text-sm font-medium mb-2">API Base URL</h3>
          <p className="text-sm text-gray-500">http://127.0.0.1:8000</p>
        </div>
        <div className="mb-4">
          <h3 className="text-sm font-medium mb-2">Allowed Origins</h3>
          <p className="text-sm text-gray-500">127.0.0.1, localhost</p>
        </div>
        <div>
          <h3 className="text-sm font-medium mb-2">Feature Flags</h3>
          <p className="text-sm text-gray-500">WhatsApp Automation: Disabled (Phase 0)</p>
          <p className="text-sm text-gray-500">OpenWA: Not Implemented</p>
          <p className="text-sm text-gray-500">Message Sending: Disabled</p>
        </div>
      </div>
    </div>
  )
}

export default Settings