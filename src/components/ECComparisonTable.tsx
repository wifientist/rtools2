import SectionDiff from '@/components/SectionDiff';

interface ECComparisonTableProps {
  source: any;
  destination: any;
}

const ECComparisonTable = ({ source, destination }: ECComparisonTableProps) => {
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