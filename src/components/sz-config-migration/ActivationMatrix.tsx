import { useMemo } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  createColumnHelper,
  flexRender,
} from '@tanstack/react-table';
import { AlertTriangle } from 'lucide-react';
import type { ResolverResult, WLANActivation, APGroupSummary } from '@/types/szConfigMigration';

interface Props {
  resolverResult: ResolverResult;
}

interface MatrixRow {
  ap_group_id: string;
  ap_group_name: string;
  ap_count: number;
  ssid_count: number;
  over_limit: boolean;
  wlans: Record<string, { radios: string[]; source: string }>;
}

export default function ActivationMatrix({ resolverResult }: Props) {
  // Build unique WLAN list (columns)
  const wlanList = useMemo(() => {
    const seen = new Map<string, { id: string; name: string; ssid: string; auth_type: string }>();
    for (const act of resolverResult.activations) {
      if (!seen.has(act.wlan_id)) {
        seen.set(act.wlan_id, {
          id: act.wlan_id,
          name: act.wlan_name,
          ssid: act.ssid,
          auth_type: act.auth_type,
        });
      }
    }
    return Array.from(seen.values());
  }, [resolverResult.activations]);

  // Build rows (one per AP Group)
  const rows: MatrixRow[] = useMemo(() => {
    const summaryMap = new Map<string, APGroupSummary>();
    for (const s of resolverResult.ap_group_summaries) {
      summaryMap.set(s.ap_group_id, s);
    }

    // Group activations by AP Group
    const byApg = new Map<string, { name: string; wlans: Record<string, { radios: string[]; source: string }> }>();
    for (const act of resolverResult.activations) {
      if (!byApg.has(act.ap_group_id)) {
        byApg.set(act.ap_group_id, { name: act.ap_group_name, wlans: {} });
      }
      byApg.get(act.ap_group_id)!.wlans[act.wlan_id] = {
        radios: act.radios,
        source: act.source,
      };
    }

    return Array.from(byApg.entries()).map(([apgId, data]) => {
      const summary = summaryMap.get(apgId);
      return {
        ap_group_id: apgId,
        ap_group_name: data.name,
        ap_count: summary?.ap_count || 0,
        ssid_count: summary?.ssid_count || 0,
        over_limit: summary?.over_limit || false,
        wlans: data.wlans,
      };
    });
  }, [resolverResult]);

  const columnHelper = createColumnHelper<MatrixRow>();

  const columns = useMemo(() => [
    columnHelper.accessor('ap_group_name', {
      header: 'AP Group',
      cell: info => (
        <div className="font-medium">
          {info.getValue()}
          <span className="text-xs text-gray-400 ml-1">({info.row.original.ap_count} APs)</span>
        </div>
      ),
    }),
    columnHelper.accessor('ssid_count', {
      header: 'SSIDs',
      cell: info => {
        const over = info.row.original.over_limit;
        return (
          <span className={`font-mono text-sm ${over ? 'text-red-600 font-bold' : ''}`}>
            {info.getValue()}/15
            {over && <AlertTriangle size={12} className="inline ml-1 text-red-500" />}
          </span>
        );
      },
    }),
    ...wlanList.map(wlan =>
      columnHelper.display({
        id: `wlan_${wlan.id}`,
        header: () => (
          <div className="text-center">
            <div className="text-xs font-medium truncate max-w-[80px]" title={wlan.name}>
              {wlan.ssid}
            </div>
            <div className="text-[10px] text-gray-400">{wlan.auth_type}</div>
          </div>
        ),
        cell: info => {
          const entry = info.row.original.wlans[wlan.id];
          if (!entry) return <div className="text-center text-gray-300">-</div>;
          return (
            <div className="text-center">
              <div className="flex justify-center gap-0.5">
                {entry.radios.map(r => (
                  <span
                    key={r}
                    className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${
                      r === '2.4' ? 'bg-yellow-100 text-yellow-700' :
                      r === '5' ? 'bg-blue-100 text-blue-700' :
                      r === '6' ? 'bg-purple-100 text-purple-700' :
                      'bg-gray-100 text-gray-600'
                    }`}
                  >
                    {r}G
                  </span>
                ))}
              </div>
              {entry.source === 'ap_group_override' && (
                <div className="text-[9px] text-orange-500 mt-0.5">override</div>
              )}
            </div>
          );
        },
      })
    ),
  ], [wlanList]);

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div>
      {resolverResult.blocked && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-3 flex items-start gap-2">
          <AlertTriangle size={16} className="text-red-500 mt-0.5 flex-shrink-0" />
          <div className="text-sm text-red-700">
            <strong>Blocked:</strong> One or more AP Groups exceed the 15-SSID limit.
            Remove SSIDs from the SZ zone before migrating.
          </div>
        </div>
      )}

      {resolverResult.warnings.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-3">
          <h4 className="text-xs font-semibold text-amber-800 mb-1">Warnings</h4>
          {resolverResult.warnings.map((w, i) => (
            <div key={i} className="text-xs text-amber-700">{w}</div>
          ))}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-gray-50">
            {table.getHeaderGroups().map(hg => (
              <tr key={hg.id}>
                {hg.headers.map(h => (
                  <th
                    key={h.id}
                    className="border border-gray-200 px-2 py-2 text-left text-xs font-semibold text-gray-600"
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map(row => (
              <tr
                key={row.id}
                className={`hover:bg-gray-50 ${row.original.over_limit ? 'bg-red-50' : ''}`}
              >
                {row.getVisibleCells().map(cell => (
                  <td key={cell.id} className="border border-gray-200 px-2 py-1.5">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-2 text-xs text-gray-400">
        Radio badges: <span className="bg-yellow-100 text-yellow-700 px-1 rounded">2.4G</span>{' '}
        <span className="bg-blue-100 text-blue-700 px-1 rounded">5G</span>{' '}
        <span className="bg-purple-100 text-purple-700 px-1 rounded">6G</span>{' '}
        | <span className="text-orange-500">override</span> = AP Group override (not zone default)
      </div>
    </div>
  );
}
