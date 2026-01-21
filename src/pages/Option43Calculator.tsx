import React, { useState } from 'react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface Option43Response {
  option_43_hex: string;
  note: string;
}

export default function Option43Calculator() {
  const [vendor, setVendor] = useState<string>('ruckus');
  const [ips, setIps] = useState<string>('');
  const [result, setResult] = useState<Option43Response | null>(null);
  const [error, setError] = useState<string | null>(null);

  const calculateOption43 = async () => {
    setError(null);
    setResult(null);

    const ipList = ips
      .split(',')
      .map(ip => ip.trim())
      .filter(ip => ip.length > 0);

    if (ipList.length === 0) {
      setError('Please enter at least one IP address.');
      return;
    }

    try {
      const res = await fetch(`${API_BASE_URL}/opt43/calculate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vendor, ip_list: ipList }),
      });

      if (!res.ok) {
        const err = await res.json();
        setError(err.detail || 'Something went wrong.');
        return;
      }

      const data: Option43Response = await res.json();
      setResult(data);
    } catch (err) {
      setError('Failed to reach backend.');
    }
  };

  return (
    <div className="max-w-md mx-auto mt-10 p-6 border rounded shadow bg-white">
      <h2 className="text-xl font-bold mb-4">Option 43 DHCP Calculator</h2>

      <label className="block mb-2 text-sm font-medium">Vendor</label>
      <select
        onChange={(e) => {
          const selection = e.target.value;
          if (selection === 'ruckus-one') {
            setVendor('ruckus');
            setIps('device.ruckus.cloud');
          } else {
            setVendor(selection);
          }
        }}
        className="w-full mb-4 p-2 border rounded"
      >
        <option value="ruckus">Ruckus (SmartZone)</option>
        <option value="ruckus-one">Ruckus ONE</option>
      </select>

      <label className="block mb-2 text-sm font-medium">
        IP Addresses or FQDN (comma separated)
      </label>
      <input
        type="text"
        value={ips}
        onChange={(e) => setIps(e.target.value)}
        placeholder="e.g. 192.168.1.10, 192.168.1.11"
        className="w-full mb-4 p-2 border rounded"
      />

      <button
        onClick={calculateOption43}
        className="w-full bg-blue-600 text-white p-2 rounded hover:bg-blue-700"
      >
        Calculate
      </button>

      {error && <div className="mt-4 text-red-600">{error}</div>}

      {result && (
        <div className="mt-4 bg-gray-100 p-4 rounded">
          <p className="font-bold">Option 43 Hex:</p>
          <code className="block mt-2 break-all">{result.option_43_hex}</code>
          <p className="text-sm mt-2 text-gray-600">{result.note}</p>
        </div>
      )}
    </div>
  );
}
