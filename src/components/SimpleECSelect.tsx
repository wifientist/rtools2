import React, { useState } from "react";

export default function SimpleECSelect({
  ecData = [],
  selectedEcId,
  onSelect,
  label,
  placeholder = "Search ECs...",
  disabled = false,
}) {
  const [searchTerm, setSearchTerm] = useState("");

  // Normalize and filter
  const filteredData = Array.isArray(ecData)
    ? ecData.filter((ec) =>
        (ec.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
         ec.tenantType?.toLowerCase().includes(searchTerm.toLowerCase()))
      )
    : [];

  return (
    <div className="mb-6">
      <label className="block text-sm font-medium mb-2">{label}</label>

      <input
        type="text"
        placeholder={placeholder}
        value={searchTerm}
        onChange={(e) => setSearchTerm(e.target.value)}
        className="input input-bordered mb-3 w-full"
        disabled={disabled}
      />

      <div className="max-h-64 overflow-y-auto border border-gray-300 rounded">
        {filteredData.length === 0 ? (
          <div className="p-4 text-gray-500 text-center">No ECs found</div>
        ) : (
          filteredData.map((ec) => {
            const ecIdStr = String(ec.id);
            const selectedIdStr = String(selectedEcId);

            return (
              <div
                key={ecIdStr}
                className={`p-3 border-b border-gray-200 cursor-pointer hover:bg-gray-50 ${
                  selectedIdStr === ecIdStr ? "bg-blue-100 border-blue-300" : ""
                } ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
                onClick={() => {
                  if (!disabled) {
                    console.log("Selected EC ID:", ecIdStr);
                    onSelect(ecIdStr);
                  }
                }}
              >
                <div className="flex justify-between items-center">
                  <div>
                    <div className="font-medium text-sm">
                      {ec.name}
                      {ec.tenantType === "SELF" && (
                        <span className="ml-2 text-xs font-semibold text-purple-600 bg-purple-100 px-2 py-0.5 rounded">
                          self
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-gray-500">
                      {ec.tenantType} • {ec.id}
                    </div>
                  </div>
                  <div className="text-xs text-gray-400">
                    {selectedIdStr === ecIdStr ? "✓ Selected" : ""}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {selectedEcId && (
        <div className="mt-2 text-sm text-blue-600">
          Selected:{" "}
          {
            ecData.find((ec) => String(ec.id) === String(selectedEcId))?.name ??
            "(unknown)"
          }
        </div>
      )}
    </div>
  );
}
