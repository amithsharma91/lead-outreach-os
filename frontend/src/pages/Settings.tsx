import React from 'react'

function Settings() {
  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <h1 className="text-3xl font-bold mb-6">Settings</h1>

      <div className="bg-white p-4 rounded-lg shadow">
        <div className="mb-4">
          <h3 className="text-sm font-medium mb-2">Database</h3>
          <p className="text-sm text-gray-500">
            SQLite production database
          </p>
        </div>

        <div className="mb-4">
          <h3 className="text-sm font-medium mb-2">API</h3>
          <p className="text-sm text-gray-500">
            Same-origin API at <code>/api</code>
          </p>
        </div>

        <div className="mb-4">
          <h3 className="text-sm font-medium mb-2">Security</h3>
          <p className="text-sm text-gray-500">
            Bearer authentication: Enabled
          </p>
          <p className="text-sm text-gray-500">
            Rate limiting: Enabled
          </p>
        </div>

        <div>
          <h3 className="text-sm font-medium mb-2">Messaging</h3>
          <p className="text-sm text-gray-500">
            WhatsApp Automation: Disabled
          </p>
          <p className="text-sm text-gray-500">
            Message Sending: Disabled
          </p>
          <p className="text-sm text-gray-500">
            Human approval: Required
          </p>
        </div>
      </div>
    </div>
  )
}

export default Settings
