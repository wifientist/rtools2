import React from "react";
import ObjectDiff from "@/components/ObjectDiff";

const SectionDiff = ({ title, sourceItems, destinationItems }) => {
  const byId = (arr) => {
    return Array.isArray(arr)
      ? Object.fromEntries(arr.map((item) => [item.id, item]))
      : {};
  };

  const srcMap = byId(sourceItems);
  const dstMap = byId(destinationItems);

  const allIds = Array.from(new Set([...Object.keys(srcMap), ...Object.keys(dstMap)]));

  return (
    <div>
      <h3 className="text-xl font-bold mb-2 capitalize">{title}</h3>
      <div className="space-y-4">
        {allIds.map((id) => (
          <ObjectDiff
            key={id}
            objectId={id}
            left={srcMap[id]}
            right={dstMap[id]}
          />
        ))}
      </div>
    </div>
  );
};

export default SectionDiff;
