import React, { useState, useMemo } from "react";
import { useSingleVenue } from "@/hooks/useSingleVenue";
import { useApGroups } from "@/hooks/useApGroups";
import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper
} from '@tanstack/react-table';

interface SingleVenueSelectorProps {
  controllerId: number | null;
  tenantId: string | number | null;
  onVenueSelect: (venueId: string | null, venue: any) => void;
  onApGroupChange?: (apGroup: string) => void;
  selectedVenueId?: string | null;
  selectedApGroup?: string;
}

export default function SingleVenueSelector({
  controllerId,
  tenantId,
  onVenueSelect,
  onApGroupChange,
  selectedVenueId,
  selectedApGroup = "Default"
}: SingleVenueSelectorProps) {
  const { venueData, loading, error } = useSingleVenue(controllerId, tenantId);
  const { apGroups, loading: loadingApGroups } = useApGroups(controllerId, tenantId);
  const [globalFilter, setGlobalFilter] = useState("");

  // Filter AP groups to only show those belonging to the selected venue
  const filteredApGroups = selectedVenueId
    ? apGroups.filter(group => group.venueId === selectedVenueId)
    : [];

  const columnHelper = createColumnHelper<any>();

  const columns = useMemo(() => [
    columnHelper.accessor('name', {
      header: 'Venue Name',
      cell: info => info.getValue()
    }),
    columnHelper.accessor('id', {
      header: 'Venue ID',
      cell: info => info.getValue()
    }),
    columnHelper.accessor('address', {
      header: 'Address',
      cell: info => {
        const address = info.getValue();
        if (!address) return 'N/A';
        if (typeof address === 'string') return address;
        // Handle address object with multiple fields
        if (typeof address === 'object') {
          const parts = [
            address.addressLine,
            address.city,
            address.country
          ].filter(Boolean);
          return parts.join(', ') || 'N/A';
        }
        return 'N/A';
      }
    }),
    columnHelper.display({
      id: 'select',
      header: 'Action',
      cell: info => {
        const venue = info.row.original;
        const isSelected = selectedVenueId === venue.id;
        return (
          <button
            className={`px-3 py-1 rounded text-sm font-medium ${
              isSelected
                ? "bg-green-600 text-white"
                : "bg-gray-200 text-gray-700 hover:bg-gray-300"
            }`}
            onClick={() => onVenueSelect(isSelected ? null : venue.id, venue)}
          >
            {isSelected ? "Selected" : "Select"}
          </button>
        );
      }
    })
  ], [selectedVenueId, onVenueSelect]);

  const table = useReactTable({
    data: venueData,
    columns,
    state: {
      globalFilter
    },
    globalFilterFn: (row, columnId, filterValue) => {
      const searchString = Object.values(row.original)
        .join(" ")
        .toLowerCase();
      return searchString.includes(filterValue.toLowerCase());
    },
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel()
  });

  if (loading) {
    return <div className="text-center p-4">Loading venues...</div>;
  }

  if (error) {
    return <div className="text-center p-4 text-red-600">Error: {error}</div>;
  }

  if (!venueData || venueData.length === 0) {
    return <div className="text-center p-4 text-gray-500">No venues found</div>;
  }

  return (
    <div className="space-y-6">
      {/* Venue Selection */}
      <div>
        <h4 className="text-lg font-semibold mb-3">Select Destination Venue</h4>
        <input
          type="text"
          placeholder="Search venues..."
          value={globalFilter}
          onChange={e => setGlobalFilter(e.target.value)}
          className="w-full px-4 py-2 border border-gray-300 rounded-lg mb-4 focus:ring-2 focus:ring-green-500 focus:border-transparent"
        />

        <div className="overflow-x-auto">
          <table className="w-full border-collapse border border-gray-300">
            <thead className="bg-gray-100">
              {table.getHeaderGroups().map(headerGroup => (
                <tr key={headerGroup.id}>
                  {headerGroup.headers.map(header => (
                    <th key={header.id} className="border border-gray-300 px-4 py-2 text-left font-semibold">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map(row => (
                <tr
                  key={row.id}
                  className={`hover:bg-gray-50 ${
                    selectedVenueId === row.original.id ? 'bg-green-50' : ''
                  }`}
                >
                  {row.getVisibleCells().map(cell => (
                    <td key={cell.id} className="border border-gray-300 px-4 py-2">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* AP Group Selection - Optional */}
      {selectedVenueId && onApGroupChange && (
        <div className="bg-gray-50 rounded-lg p-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            AP Group (Optional)
          </label>
          {loadingApGroups ? (
            <div className="text-sm text-gray-500">Loading AP groups...</div>
          ) : (
            <>
              <select
                value={selectedApGroup}
                onChange={(e) => onApGroupChange(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent"
              >
                <option value="Default">Default</option>
                {filteredApGroups && filteredApGroups.map((group: any, idx: number) => {
                  console.log(`🎨 Rendering AP Group ${idx} for venue ${selectedVenueId}:`, group);
                  return (
                    <option key={group.id || idx} value={group.name || ''}>
                      {group.name || `(Unnamed Group ${group.id || idx})`}
                    </option>
                  );
                })}
              </select>
              {process.env.NODE_ENV === 'development' && (
                <div className="text-xs text-gray-400 mt-1">
                  Debug: {filteredApGroups?.length || 0} AP groups for this venue (out of {apGroups?.length || 0} total)
                </div>
              )}
            </>
          )}
          <p className="text-sm text-gray-500 mt-1">
            Select the AP Group for migrated Access Points (defaults to "Default")
          </p>
        </div>
      )}
    </div>
  );
}
