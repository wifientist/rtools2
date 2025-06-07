import React, { useState, useMemo } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper
} from '@tanstack/react-table';

// Your ECSelector component
export default function EcSelector({ ecData }) {
  const [globalFilter, setGlobalFilter] = useState("");
  const [selectedEcId, setSelectedEcId] = useState(null);

  // Define the columns with TanStack's helper
  const columnHelper = createColumnHelper();

  const columns = useMemo(() => [
    columnHelper.accessor('name', {
      header: 'Name',
      cell: info => info.getValue()
    }),
    columnHelper.accessor('tenantType', {
      header: 'Type'
    }),
    columnHelper.accessor('mspAdminCount', {
      header: 'Admins'
    }),
    columnHelper.accessor('installerCount', {
      header: 'Installers'
    }),
    columnHelper.accessor('integratorCount', {
      header: 'Integrators'
    }),
    columnHelper.accessor('customerCount', {
      header: 'Customers'
    }),
    columnHelper.display({
      id: 'select',
      header: 'Action',
      cell: info => {
        const rowId = info.row.original.id;
        return (
          <button
            className={`btn btn-sm ${selectedEcId === rowId ? "btn-primary" : "btn-outline"}`}
            onClick={() => setSelectedEcId(rowId)}
          >
            {selectedEcId === rowId ? "Selected" : "Select"}
          </button>
        );
      }
    })
  ], [selectedEcId]);

  const table = useReactTable({
    data: ecData,
    columns,
    state: {
      globalFilter
    },
    globalFilterFn: (row, columnId, filterValue) => {
      // Global search across all relevant columns
      const searchString = Object.values(row.original)
        .join(" ")
        .toLowerCase();
      return searchString.includes(filterValue.toLowerCase());
    },
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel()
  });

  return (
    <div>
      <input
        type="text"
        placeholder="Search ECs..."
        value={globalFilter}
        onChange={e => setGlobalFilter(e.target.value)}
        className="input input-bordered mb-4 w-full"
      />

      <table className="table-auto w-full border-collapse border border-gray-400">
        <thead>
          {table.getHeaderGroups().map(headerGroup => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map(header => (
                <th key={header.id} className="border border-gray-300 px-2 py-1">
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map(row => (
            <tr key={row.id} className="hover:bg-gray-100">
              {row.getVisibleCells().map(cell => (
                <td key={cell.id} className="border border-gray-300 px-2 py-1">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
