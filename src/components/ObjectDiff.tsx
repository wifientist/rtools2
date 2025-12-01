import { shouldIgnoreField, isImportantField } from "@/utils/diffConfig";

function formatValue(val: any): string {
  if (val === null || val === undefined) {
    return "—";
  }

  if (Array.isArray(val)) {
    if (val.length === 0) return "[]";
    // For primitive arrays, show them inline
    if (val.every(item => typeof item !== 'object')) {
      return JSON.stringify(val);
    }
    // This shouldn't happen after flattening, but just in case
    return `[${val.length} items]`;
  }

  if (typeof val === "object") {
    // Show small objects inline
    return JSON.stringify(val);
  }

  if (typeof val === "boolean") {
    return val ? "true" : "false";
  }

  return String(val);
}

function flatten(obj: any, prefix = "", sectionType = "", depth = 0, maxDepth = 10): Record<string, any> {
  // Prevent infinite recursion
  if (depth > maxDepth) {
    return { [prefix || 'root']: obj };
  }

  return Object.entries(obj || {}).reduce((acc, [key, val]) => {
    const newKey = prefix ? `${prefix}.${key}` : key;

    // Filter out ignored fields during flattening
    if (shouldIgnoreField(newKey)) {
      return acc;
    }

    if (Array.isArray(val)) {
      if (val.length === 0) {
        acc[newKey] = [];
      } else if (typeof val[0] === 'object' && val[0] !== null) {
        // Flatten each object in the array
        val.forEach((item, index) => {
          const itemKey = `${newKey}[${index}]`;
          Object.assign(acc, flatten(item, itemKey, sectionType, depth + 1, maxDepth));
        });
      } else {
        // Primitive array - store as-is
        acc[newKey] = val;
      }
    } else if (typeof val === "object" && val !== null) {
      // Recursively flatten all objects
      Object.assign(acc, flatten(val, newKey, sectionType, depth + 1, maxDepth));
    } else {
      // Primitive value
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
  showOnlyDifferences?: boolean;
}

const ObjectDiff = ({ matchInfo, sectionType, showOnlyDifferences = false }: ObjectDiffProps) => {
  const { source, dest, score, matchType } = matchInfo;

  const leftFlat = flatten(source, "", sectionType);
  const rightFlat = flatten(dest, "", sectionType);

  const allKeys = Array.from(new Set([...Object.keys(leftFlat), ...Object.keys(rightFlat)])).sort();

  // Filter keys to only show differences if toggle is enabled
  const filteredKeys = showOnlyDifferences
    ? allKeys.filter(key => {
        const leftVal = leftFlat[key];
        const rightVal = rightFlat[key];
        return JSON.stringify(leftVal) !== JSON.stringify(rightVal);
      })
    : allKeys;

  const hiddenCount = allKeys.length - filteredKeys.length;

  // Calculate difference count
  const differenceCount = allKeys.filter(key => {
    const leftVal = leftFlat[key];
    const rightVal = rightFlat[key];
    return JSON.stringify(leftVal) !== JSON.stringify(rightVal);
  }).length;

  const identicalCount = allKeys.length - differenceCount;

  // Format similarity percentage with appropriate precision
  const formatSimilarity = (score: number) => {
    if (score === 1.0) return '100';
    if (score >= 0.995) return (score * 100).toFixed(2);
    if (score >= 0.95) return (score * 100).toFixed(1);
    return Math.round(score * 100).toString();
  };

  // Determine match type styling and display name
  const getMatchTypeInfo = () => {
    switch (matchType) {
      case 'matched':
        return {
          bgColor: 'bg-green-50',
          borderColor: 'border-green-200',
          badgeColor: 'bg-green-100 text-green-800',
          label: `Matched (${formatSimilarity(score)}% similarity)`,
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

      {/* Always show field count summary */}
      <div className="mb-3 text-sm text-gray-600 bg-white rounded p-2 border border-gray-200">
        <div className="flex items-center gap-4">
          <span>📊 {allKeys.length} total field{allKeys.length !== 1 ? 's' : ''}</span>
          <span className="text-red-600">• {differenceCount} difference{differenceCount !== 1 ? 's' : ''}</span>
          <span className="text-green-600">• {identicalCount} identical</span>
          {showOnlyDifferences && hiddenCount > 0 && (
            <span className="text-blue-600">• (hiding {hiddenCount} identical)</span>
          )}
        </div>
      </div>

      {filteredKeys.length > 0 ? (
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-gray-300">
              <th className="w-1/3 text-left py-2 px-2 font-semibold">Field</th>
              <th className="w-1/3 text-left py-2 px-2 font-semibold">Source</th>
              <th className="w-1/3 text-left py-2 px-2 font-semibold">Destination</th>
            </tr>
          </thead>
          <tbody>
            {filteredKeys.map((key) => {
              const leftVal = leftFlat[key];
              const rightVal = rightFlat[key];

              // Deep comparison for objects/arrays
              const isDiff = JSON.stringify(leftVal) !== JSON.stringify(rightVal);
              const isImportant = isImportantField(key);

              return (
                <tr
                  key={key}
                  className={`border-b border-gray-200 ${isDiff ? 'bg-yellow-100' : ''} ${isImportant ? 'font-semibold' : ''}`}
                >
                  <td className="py-2 px-2 font-mono text-gray-700 align-top">{key}</td>
                  <td className="py-2 px-2 break-words whitespace-pre-wrap align-top max-w-md">{formatValue(leftVal)}</td>
                  <td className="py-2 px-2 break-words whitespace-pre-wrap align-top max-w-md">{formatValue(rightVal)}</td>
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
