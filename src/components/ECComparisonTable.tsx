import React from 'react';
import SectionDiff from '@/components/SectionDiff';

const ECComparisonTable = ({ source, destination }) => {
    const srcData = source?.data || {};
    const dstData = destination?.data || {};
  
    const allSections = Array.from(new Set([
      ...Object.keys(srcData),
      ...Object.keys(dstData)
    ]));
  
    return (
      <div className="space-y-8">
        {allSections.map((sectionKey) => {
          const sourceItems = srcData[sectionKey] || [];
          const destinationItems = dstData[sectionKey] || [];
  
          // Skip "aps" for now
          if (sectionKey === "aps") return null;
  
          return (
            <SectionDiff
              key={sectionKey}
              title={sectionKey}
              sourceItems={sourceItems}
              destinationItems={destinationItems}
            />
          );
        })}
      </div>
    );
  };
  
  export default ECComparisonTable;