import React, { useState, useMemo, useEffect } from "react";
import { useDpskPools } from "@/hooks/useDpskPools";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper
} from '@tanstack/react-table';

interface DPSKPool {
  id: string;
  name: string;
  description?: string;
  identityGroupId?: string;
  identityGroupName?: string;
  maxDevicesPerUser?: number;
  passphraseCount?: number;
}

// Single-select props
interface SingleSelectProps {
  multiSelect?: false;
  onPoolSelect: (poolId: string | null, pool: DPSKPool | null) => void;
  selectedPoolId?: string | null;
  selectedPoolIds?: never;
  onPoolsSelect?: never;
}

// Multi-select props
interface MultiSelectProps {
  multiSelect: true;
  onPoolsSelect: (poolIds: string[], pools: DPSKPool[]) => void;
  selectedPoolIds: string[];
  selectedPoolId?: never;
  onPoolSelect?: never;
}

type DpskPoolSelectorProps = {
  controllerId: number | null;
  tenantId: string | null;
  excludePoolId?: string | null;  // Exclude a pool (e.g., destination pool)
  initialFilter?: string;  // Pre-populate include filter (glob pattern)
  initialExcludeFilter?: string;  // Pre-populate exclude filter (glob pattern)
} & (SingleSelectProps | MultiSelectProps);

// Pattern matching - supports glob (* wildcard) or substring search
function matchesPattern(text: string, pattern: string): boolean {
  if (!pattern) return true;

  // If pattern contains *, use glob matching
  if (pattern.includes('*')) {
    const regex = new RegExp(
      '^' + pattern.split('*').map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$',
      'i'
    );
    return regex.test(text);
  }

  // Otherwise, use substring matching (case-insensitive)
  return text.toLowerCase().includes(pattern.toLowerCase());
}

export default function DpskPoolSelector(props: DpskPoolSelectorProps) {
  const {
    controllerId,
    tenantId,
    excludePoolId,
    initialFilter = "",
    initialExcludeFilter = "",
    multiSelect
  } = props;

  const { pools: allPools, loading, error } = useDpskPools(controllerId, tenantId);
  const [includeFilter, setIncludeFilter] = useState(initialFilter);
  const [excludeFilter, setExcludeFilter] = useState(initialExcludeFilter);

  // Update filters when initial values change
  useEffect(() => {
    if (initialFilter) {
      setIncludeFilter(initialFilter);
    }
  }, [initialFilter]);

  useEffect(() => {
    if (initialExcludeFilter) {
      setExcludeFilter(initialExcludeFilter);
    }
  }, [initialExcludeFilter]);

  // Filter out excluded pool and apply include/exclude patterns
  const pools = useMemo(() => {
    let filtered = allPools;

    // Remove the explicitly excluded pool (e.g., destination pool)
    if (excludePoolId) {
      filtered = filtered.filter(p => p.id !== excludePoolId);
    }

    return filtered;
  }, [allPools, excludePoolId]);

  // Apply include/exclude filters to get visible pools
  const filteredPools = useMemo(() => {
    return pools.filter(pool => {
      const name = pool.name || '';

      // If include filter is set, pool must match it
      if (includeFilter && !matchesPattern(name, includeFilter)) {
        return false;
      }

      // If exclude filter is set, pool must NOT match it
      if (excludeFilter && matchesPattern(name, excludeFilter)) {
        return false;
      }

      return true;
    });
  }, [pools, includeFilter, excludeFilter]);

  const columnHelper = createColumnHelper<DPSKPool>();

  // Check if a pool is selected
  const isPoolSelected = (poolId: string): boolean => {
    if (multiSelect) {
      return props.selectedPoolIds.includes(poolId);
    }
    return props.selectedPoolId === poolId;
  };

  // Handle pool toggle
  const handlePoolToggle = (pool: DPSKPool) => {
    if (multiSelect) {
      const currentIds = props.selectedPoolIds;
      if (currentIds.includes(pool.id)) {
        // Remove from selection
        const newIds = currentIds.filter(id => id !== pool.id);
        const newPools = pools.filter(p => newIds.includes(p.id));
        props.onPoolsSelect(newIds, newPools);
      } else {
        // Add to selection
        const newIds = [...currentIds, pool.id];
        const newPools = pools.filter(p => newIds.includes(p.id));
        props.onPoolsSelect(newIds, newPools);
      }
    } else {
      const isSelected = props.selectedPoolId === pool.id;
      props.onPoolSelect(isSelected ? null : pool.id, isSelected ? null : pool);
    }
  };

  // Select all visible (filtered) pools
  const handleSelectAllVisible = () => {
    if (!multiSelect) return;

    const currentIds = props.selectedPoolIds;
    const visibleIds = filteredPools.map(p => p.id);

    // Add all visible pools that aren't already selected
    const newIds = [...new Set([...currentIds, ...visibleIds])];
    const newPools = pools.filter(p => newIds.includes(p.id));
    props.onPoolsSelect(newIds, newPools);
  };

  // Check if all visible pools are already selected
  const allVisibleSelected = multiSelect && filteredPools.length > 0 &&
    filteredPools.every(p => props.selectedPoolIds.includes(p.id));

  const columns = useMemo(() => [
    columnHelper.accessor('name', {
      header: 'Pool Name',
      cell: info => info.getValue() || '(Unnamed)'
    }),
    columnHelper.accessor('identityGroupName', {
      header: 'Identity Group',
      cell: info => info.getValue() || '-'
    }),
    columnHelper.display({
      id: 'select',
      header: multiSelect ? 'Include' : 'Action',
      cell: info => {
        const pool = info.row.original;
        const selected = isPoolSelected(pool.id);
        return (
          <button
            className={`px-3 py-1 rounded text-sm font-medium ${
              selected
                ? "bg-green-600 text-white"
                : "bg-gray-200 text-gray-700 hover:bg-gray-300"
            }`}
            onClick={() => handlePoolToggle(pool)}
          >
            {selected ? (multiSelect ? "✓ Included" : "Selected") : (multiSelect ? "Include" : "Select")}
          </button>
        );
      }
    })
  ], [multiSelect, props]);

  const table = useReactTable({
    data: filteredPools,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel()
  });

  if (loading) {
    return <div className="text-center p-4">Loading DPSK pools...</div>;
  }

  if (error) {
    return <div className="text-center p-4 text-red-600">Error: {error}</div>;
  }

  if (!pools || pools.length === 0) {
    return <div className="text-center p-4 text-gray-500">No DPSK pools found</div>;
  }

  const selectedCount = multiSelect ? props.selectedPoolIds.length : (props.selectedPoolId ? 1 : 0);

  return (
    <div className="space-y-4">
      {/* Filter inputs */}
      <div className={multiSelect ? "grid grid-cols-2 gap-3" : ""}>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">
            {multiSelect ? "Include Pattern" : "Search"}
          </label>
          <input
            type="text"
            placeholder={multiSelect ? "e.g., Unit*" : "Search DPSK pools..."}
            value={includeFilter}
            onChange={e => setIncludeFilter(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm"
          />
        </div>
        {multiSelect && (
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Exclude Pattern
            </label>
            <input
              type="text"
              placeholder="e.g., SiteWide*"
              value={excludeFilter}
              onChange={e => setExcludeFilter(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-red-500 focus:border-transparent text-sm"
            />
          </div>
        )}
      </div>

      {/* Selection controls for multi-select */}
      {multiSelect && (
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm">
            {selectedCount > 0 && (
              <>
                <span className="px-2 py-1 bg-green-100 text-green-800 rounded-full font-medium">
                  {selectedCount} pool{selectedCount !== 1 ? 's' : ''} selected
                </span>
                <button
                  onClick={() => props.onPoolsSelect([], [])}
                  className="text-gray-500 hover:text-gray-700 text-xs underline"
                >
                  Clear all
                </button>
              </>
            )}
          </div>
          <button
            onClick={handleSelectAllVisible}
            disabled={allVisibleSelected || filteredPools.length === 0}
            className="px-3 py-1.5 text-sm bg-indigo-100 text-indigo-800 rounded-lg hover:bg-indigo-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {allVisibleSelected ? "All Visible Selected" : `Select All Visible (${filteredPools.length})`}
          </button>
        </div>
      )}

      <div className="overflow-x-auto max-h-64 overflow-y-auto border border-gray-300 rounded-lg">
        <table className="w-full border-collapse">
          <thead className="bg-gray-100 sticky top-0">
            {table.getHeaderGroups().map(headerGroup => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map(header => (
                  <th key={header.id} className="border-b border-gray-300 px-4 py-2 text-left text-sm font-semibold text-gray-700">
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
                  isPoolSelected(row.original.id) ? 'bg-green-50' : ''
                }`}
              >
                {row.getVisibleCells().map(cell => (
                  <td key={cell.id} className="border-b border-gray-200 px-4 py-2 text-sm">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-gray-500">
        {filteredPools.length} of {pools.length} pools shown
        {excludePoolId && " (destination pool excluded)"}
        {(includeFilter || excludeFilter) && " (filtered)"}
      </p>
    </div>
  );
}
