/**
 * Smart matching algorithm for finding corresponding items across different ECs
 */

import { getMatchingFields } from './diffConfig';

/**
 * Get nested value from object using dot notation path
 */
function getNestedValue(obj: any, path: string): any {
  return path.split('.').reduce((current, key) => current?.[key], obj);
}

/**
 * Calculate similarity score between two items (0-1, where 1 is identical)
 */
export function calculateSimilarity(item1: any, item2: any, matchingFields: string[]): number {
  if (!item1 || !item2) return 0;

  let matchedFields = 0;
  let totalFields = 0;

  for (const field of matchingFields) {
    const val1 = getNestedValue(item1, field);
    const val2 = getNestedValue(item2, field);

    // Skip if both values are null/undefined
    if (val1 == null && val2 == null) continue;

    totalFields++;

    if (val1 === val2) {
      matchedFields++;
    } else if (typeof val1 === 'string' && typeof val2 === 'string') {
      // Partial string matching (case-insensitive)
      const similarity = stringSimilarity(val1.toLowerCase(), val2.toLowerCase());
      matchedFields += similarity;
    }
  }

  return totalFields > 0 ? matchedFields / totalFields : 0;
}

/**
 * Simple string similarity (Dice coefficient)
 */
function stringSimilarity(str1: string, str2: string): number {
  if (str1 === str2) return 1;
  if (str1.length < 2 || str2.length < 2) return 0;

  const bigrams1 = getBigrams(str1);
  const bigrams2 = getBigrams(str2);

  const intersection = bigrams1.filter(bigram => bigrams2.includes(bigram));

  return (2 * intersection.length) / (bigrams1.length + bigrams2.length);
}

/**
 * Get bigrams from a string
 */
function getBigrams(str: string): string[] {
  const bigrams: string[] = [];
  for (let i = 0; i < str.length - 1; i++) {
    bigrams.push(str.substring(i, i + 2));
  }
  return bigrams;
}

/**
 * Match items from source and destination lists
 * Returns array of matched pairs: [sourceItem, destItem, similarityScore]
 */
export function matchItems(
  sourceItems: any[],
  destItems: any[],
  sectionType: string,
  similarityThreshold: number = 0.5
): Array<{ source: any | null; dest: any | null; score: number; matchType: 'matched' | 'source-only' | 'dest-only' }> {
  const matchingFields = getMatchingFields(sectionType);
  const results: Array<{ source: any | null; dest: any | null; score: number; matchType: 'matched' | 'source-only' | 'dest-only' }> = [];
  const usedDestIndices = new Set<number>();

  // For each source item, find best matching dest item
  for (const sourceItem of sourceItems) {
    let bestMatch = { index: -1, score: 0 };

    destItems.forEach((destItem, destIndex) => {
      if (usedDestIndices.has(destIndex)) return;

      const score = calculateSimilarity(sourceItem, destItem, matchingFields);
      if (score > bestMatch.score) {
        bestMatch = { index: destIndex, score };
      }
    });

    if (bestMatch.score >= similarityThreshold) {
      usedDestIndices.add(bestMatch.index);
      results.push({
        source: sourceItem,
        dest: destItems[bestMatch.index],
        score: bestMatch.score,
        matchType: 'matched'
      });
    } else {
      // No good match found - source only
      results.push({
        source: sourceItem,
        dest: null,
        score: 0,
        matchType: 'source-only'
      });
    }
  }

  // Add remaining dest items that weren't matched
  destItems.forEach((destItem, destIndex) => {
    if (!usedDestIndices.has(destIndex)) {
      results.push({
        source: null,
        dest: destItem,
        score: 0,
        matchType: 'dest-only'
      });
    }
  });

  // Sort by match quality: matched items first (by score), then source-only, then dest-only
  results.sort((a, b) => {
    if (a.matchType === 'matched' && b.matchType === 'matched') {
      return b.score - a.score; // Higher scores first
    }
    if (a.matchType === 'matched') return -1;
    if (b.matchType === 'matched') return 1;
    if (a.matchType === 'source-only') return -1;
    return 1;
  });

  return results;
}
