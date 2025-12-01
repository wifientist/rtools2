const Home = () => {
  return (
    <div className="max-w-4xl mx-auto p-8">
      {/* Header */}
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold text-gray-900 mb-3">RUCKUS.Tools</h1>
        <p className="text-xl text-gray-600">Network management and diagnostics tools for Ruckus ONE deployments</p>
      </div>

      {/* Overview */}
      <section className="mb-12 bg-white rounded-lg shadow p-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-4">What We Do</h2>
        <p className="text-gray-700 leading-relaxed mb-4">
          RUCKUS.Tools provides a suite of utilities designed to simplify network operations,
          migrations, and troubleshooting for Ruckus ONE wireless deployments. 
          The majority of the tools are behind authentication walls to ensure secure access.  Only approved
          email domains are allowed to register for an account.  Login is enabled through One Time Passwords (OTP)
          sent via email to ensure secure access to current employees without the need for passwords.
        </p>
        <p className="text-gray-700 leading-relaxed mb-4">
          Please note that RUCKUS.Tools is an independent project and is not affiliated with or endorsed
          by CommScope or Ruckus Networks.  These are tools built by Ruckus engineers that might be 
          useful to the wider community.
        </p>
        <p className="text-gray-700 leading-relaxed mb-4">
          
        </p>
        
      </section>

      {/* Key Tools */}
      <section className="mb-12">
        <h2 className="text-2xl font-bold text-gray-900 mb-6">Available Tools</h2>

        <div className="grid gap-4 md:grid-cols-2">
          {/* Diff Tools */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-5">
            <h3 className="text-lg font-semibold text-blue-900 mb-2">🔍 Comparison Tools</h3>
            <p className="text-sm text-blue-800">
              Compare configurations between tenants and venues. Identify differences in WLANs,
              APs, AP Groups, and WiFi settings with intelligent matching and detailed field-level analysis.
            </p>
          </div>

          {/* Speed Explainer */}
          <div className="bg-purple-50 border border-purple-200 rounded-lg p-5">
            <h3 className="text-lg font-semibold text-purple-900 mb-2">📊 Speed Explainer</h3>
            <p className="text-sm text-purple-800">
              Interactive WiFi performance diagnostics. Understand why speeds are slow by analyzing
              signal quality, airtime utilization, interference, backhaul, and client capabilities.
            </p>
          </div>

          {/* Migration Tools */}
          <div className="bg-green-50 border border-green-200 rounded-lg p-5">
            <h3 className="text-lg font-semibold text-green-900 mb-2">🚀 Migration Assistant</h3>
            <p className="text-sm text-green-800">
              Streamline migrations from SmartZone to Ruckus ONE. Automate tenant creation,
              configuration mapping, and bulk operations with intelligent conflict resolution.
            </p>
          </div>

          {/* Diagramming */}
          <div className="bg-orange-50 border border-orange-200 rounded-lg p-5">
            <h3 className="text-lg font-semibold text-orange-900 mb-2">🎨 Network Diagramming</h3>
            <p className="text-sm text-orange-800">
              Integrated fossFLOW diagramming tool for visualizing network topologies,
              documenting deployments, and creating technical diagrams.
            </p>
          </div>
        </div>
      </section>

      {/* Technology Stack */}
      <section className="mb-12 bg-gray-50 rounded-lg p-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-4">Built With</h2>
        <div className="grid md:grid-cols-2 gap-4 text-sm text-gray-700">
          <div>
            <p className="font-semibold mb-2">Frontend</p>
            <ul className="list-disc list-inside space-y-1">
              <li>React with TypeScript</li>
              <li>Vite for fast builds</li>
              <li>TailwindCSS for styling</li>
            </ul>
          </div>
          <div>
            <p className="font-semibold mb-2">Backend</p>
            <ul className="list-disc list-inside space-y-1">
              <li>FastAPI (Python)</li>
              <li>PostgreSQL database</li>
              <li>JWT authentication</li>
            </ul>
          </div>
        </div>
      </section>

      {/* Contributors & Inspiration */}
      <section className="mb-8 bg-white rounded-lg shadow p-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-4">Contributors & Inspiration</h2>

        <div className="space-y-4 text-gray-700">
          <div>
            <h3 className="font-semibold text-gray-900 mb-1">Open Source Projects</h3>
            <ul className="list-disc list-inside space-y-1 text-sm">
              <li>
                <a href="https://github.com/stan-smith/fossFLOW" target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                  fossFLOW
                </a> - Network diagramming tool by Stan Smith
              </li>
            </ul>
          </div>

          <div>
            <h3 className="font-semibold text-gray-900 mb-1">Special Thanks</h3>
            <p className="text-sm">
              To the network engineering community for feedback, feature requests, and real-world
              testing that drives continuous improvement of these tools.
            </p>
          </div>
        </div>
      </section>

      {/* Footer */}
      <div className="text-center text-sm text-gray-500">
        <p>Have ideas or feedback? <a href="https://github.com/anthropics/claude-code/issues" className="text-blue-600 hover:underline">Open an issue on GitHub</a></p>
      </div>
    </div>
  );
};

export default Home;
