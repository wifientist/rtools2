import React from "react";

function flatten(obj, prefix = "") {
  return Object.entries(obj || {}).reduce((acc, [key, val]) => {
    const newKey = prefix ? `${prefix}.${key}` : key;
    if (typeof val === "object" && val !== null && !Array.isArray(val)) {
      Object.assign(acc, flatten(val, newKey));
    } else {
      acc[newKey] = val;
    }
    return acc;
  }, {});
}

const ObjectDiff = ({ objectId, left, right }) => {
  const leftFlat = flatten(left);
  const rightFlat = flatten(right);

  const allKeys = Array.from(new Set([...Object.keys(leftFlat), ...Object.keys(rightFlat)])).sort();

  return (
    <div className="border rounded shadow p-4 bg-white">
      <h4 className="font-semibold mb-2">ID: {objectId}</h4>
      <table className="table is-bordered is-fullwidth text-xs">
        <thead>
          <tr>
            <th className="w-1/3">Field</th>
            <th className="w-1/3">Source</th>
            <th className="w-1/3">Destination</th>
          </tr>
        </thead>
        <tbody>
          {allKeys.map((key) => {
            const leftVal = leftFlat[key];
            const rightVal = rightFlat[key];
            const isDiff = leftVal !== rightVal;

            return (
              <tr key={key} className={isDiff ? "has-background-warning-light" : ""}>
                <td className="font-mono">{key}</td>
                <td>{String(leftVal ?? "")}</td>
                <td>{String(rightVal ?? "")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default ObjectDiff;
