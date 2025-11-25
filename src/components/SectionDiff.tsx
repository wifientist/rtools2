import { useMemo } from "react";
import ObjectDiff from "@/components/ObjectDiff";
import { matchItems } from "@/utils/smartMatcher";

interface SectionDiffProps {
  title: string;
  sourceItems: any[];
  destinationItems: any[];
}

const SectionDiff = ({ title, sourceItems, destinationItems }: SectionDiffProps) => {
  const matches = useMemo(() => {
    const srcArray = Array.isArray(sourceItems) ? sourceItems : [];
    const dstArray = Array.isArray(destinationItems) ? destinationItems : [];

    return matchItems(srcArray, dstArray, title);
  }, [sourceItems, destinationItems, title]);

  // Summary statistics
  const stats = useMemo(() => {
    const matched = matches.filter(m => m.matchType === 'matched').length;
    const sourceOnly = matches.filter(m => m.matchType === 'source-only').length;
    const destOnly = matches.filter(m => m.matchType === 'dest-only').length;

    return { matched, sourceOnly, destOnly, total: matches.length };
  }, [matches]);

  if (matches.length === 0) {
    return null; // Don't show empty sections
  }

  return (
    <div className="mb-8">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-2xl font-bold capitalize">{title}</h3>
        <div className="flex gap-4 text-sm">
          <span className="px-3 py-1 bg-green-100 text-green-800 rounded-full">
            ✓ {stats.matched} matched
          </span>
          {stats.sourceOnly > 0 && (
            <span className="px-3 py-1 bg-blue-100 text-blue-800 rounded-full">
              ← {stats.sourceOnly} source only
            </span>
          )}
          {stats.destOnly > 0 && (
            <span className="px-3 py-1 bg-purple-100 text-purple-800 rounded-full">
              → {stats.destOnly} dest only
            </span>
          )}
        </div>
      </div>

      <div className="space-y-4">
        {matches.map((match, index) => (
          <ObjectDiff
            key={index}
            matchInfo={match}
            sectionType={title}
          />
        ))}
      </div>
    </div>
  );
};

export default SectionDiff;
