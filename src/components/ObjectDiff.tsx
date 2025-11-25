import { shouldIgnoreField, isImportantField } from "@/utils/diffConfig";

function flatten(obj: any, prefix = "", sectionType = ""): Record<string, any> {
  return Object.entries(obj || {}).reduce((acc, [key, val]) => {
    const newKey = prefix ? `${prefix}.${key}` : key;

    // Filter out ignored fields during flattening
    if (shouldIgnoreField(newKey)) {
      return acc;
    }

    if (typeof val === "object" && val !== null && !Array.isArray(val)) {
      Object.assign(acc, flatten(val, newKey, sectionType));
    } else {
      acc[newKey] = val;
    }
    return acc;
  }, {} as Record<string, any>);
}

interface MatchInfo {
  source: any | null;
  dest: any | null;
  score: number;
  matchType: 'matched' | 'source-only' | 'dest-only';
}

interface ObjectDiffProps {
  matchInfo: MatchInfo;
  sectionType: string;
}

const ObjectDiff = ({ matchInfo, sectionType }: ObjectDiffProps) => {
  const { source, dest, score, matchType } = matchInfo;

  const leftFlat = flatten(source, "", sectionType);
  const rightFlat = flatten(dest, "", sectionType);

  const allKeys = Array.from(new Set([...Object.keys(leftFlat), ...Object.keys(rightFlat)])).sort();

  // Determine match type styling and display name
  const getMatchTypeInfo = () => {
    switch (matchType) {
      case 'matched':
        return {
          bgColor: 'bg-green-50',
          borderColor: 'border-green-200',
          badgeColor: 'bg-green-100 text-green-800',
          label: `Matched (${Math.round(score * 100)}% similarity)`,
          icon: '✓'
        };
      case 'source-only':
        return {
          bgColor: 'bg-blue-50',
          borderColor: 'border-blue-200',
          badgeColor: 'bg-blue-100 text-blue-800',
          label: 'Source Only',
          icon: '←'
        };
      case 'dest-only':
        return {
          bgColor: 'bg-purple-50',
          borderColor: 'border-purple-200',
          badgeColor: 'bg-purple-100 text-purple-800',
          label: 'Destination Only',
          icon: '→'
        };
    }
  };

  const matchTypeInfo = getMatchTypeInfo();

  // Get display name for the item
  const getDisplayName = () => {
    // For matched items with imperfect similarity, show both names
    if (matchType === 'matched' && source && dest && score < 1.0) {
      const sourceName = source?.name || source?.ssid || source?.serialNumber || source?.mac || 'Unknown';
      const destName = dest?.name || dest?.ssid || dest?.serialNumber || dest?.mac || 'Unknown';

      // Only show both if they're actually different
      if (sourceName !== destName) {
        return `${sourceName} ↔ ${destName}`;
      }
      return sourceName;
    }

    // For source-only, dest-only, or perfect matches, show single name
    const item = source || dest;
    return item?.name || item?.ssid || item?.serialNumber || item?.mac || 'Unknown';
  };

  return (
    <div className={`border rounded-lg shadow p-4 ${matchTypeInfo.bgColor} ${matchTypeInfo.borderColor}`}>
      <div className="flex items-center justify-between mb-3">
        <h4 className="font-semibold text-lg">{getDisplayName()}</h4>
        <span className={`px-3 py-1 rounded-full text-sm font-medium ${matchTypeInfo.badgeColor}`}>
          {matchTypeInfo.icon} {matchTypeInfo.label}
        </span>
      </div>

      {allKeys.length > 0 ? (
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-gray-300">
              <th className="w-1/3 text-left py-2 px-2 font-semibold">Field</th>
              <th className="w-1/3 text-left py-2 px-2 font-semibold">Source</th>
              <th className="w-1/3 text-left py-2 px-2 font-semibold">Destination</th>
            </tr>
          </thead>
          <tbody>
            {allKeys.map((key) => {
              const leftVal = leftFlat[key];
              const rightVal = rightFlat[key];
              const isDiff = leftVal !== rightVal;
              const isImportant = isImportantField(key);

              return (
                <tr
                  key={key}
                  className={`border-b border-gray-200 ${isDiff ? 'bg-yellow-100' : ''} ${isImportant ? 'font-semibold' : ''}`}
                >
                  <td className="py-2 px-2 font-mono text-gray-700">{key}</td>
                  <td className="py-2 px-2 break-words">{String(leftVal ?? "—")}</td>
                  <td className="py-2 px-2 break-words">{String(rightVal ?? "—")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <div className="text-sm text-gray-500 italic py-4 text-center">
          No fields to compare after filtering
        </div>
      )}
    </div>
  );
};

export default ObjectDiff;
