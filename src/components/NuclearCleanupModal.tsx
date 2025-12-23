import React, { useState, useEffect } from 'react';
import { ChevronDown, ChevronRight, AlertTriangle, Loader } from 'lucide-react';

interface ResourceItem {
  id: string;
  name?: string;
  username?: string;
  pool_id?: string;
}

interface ResourceInventory {
  passphrases: ResourceItem[];
  dpsk_pools: ResourceItem[];
  identities: ResourceItem[];
  identity_groups: ResourceItem[];
}

interface NuclearCleanupModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  controllerId: number;
  venueId: string;
  venueName: string;
  tenantId?: string;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api';

const NuclearCleanupModal: React.FC<NuclearCleanupModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
  controllerId,
  venueId,
  venueName,
  tenantId
}) => {
  const [loading, setLoading] = useState(true);
  const [inventory, setInventory] = useState<ResourceInventory | null>(null);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [selectedCategories, setSelectedCategories] = useState<Set<string>>(
    new Set(['passphrases', 'dpsk_pools', 'identities', 'identity_groups'])
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      fetchPreview();
    }
  }, [isOpen, controllerId, venueId]);

  const fetchPreview = async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/cloudpath-dpsk/preview-cleanup`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          controller_id: controllerId,
          venue_id: venueId,
          tenant_id: tenantId
        })
      });

      if (!response.ok) {
        throw new Error(`Failed to preview cleanup: ${response.statusText}`);
      }

      const result = await response.json();
      setInventory(result.inventory);
    } catch (err) {
      console.error('Error fetching cleanup preview:', err);
      setError(err instanceof Error ? err.message : 'Failed to load preview');
    } finally {
      setLoading(false);
    }
  };

  const toggleCategory = (category: string) => {
    const newExpanded = new Set(expandedCategories);
    if (newExpanded.has(category)) {
      newExpanded.delete(category);
    } else {
      newExpanded.add(category);
    }
    setExpandedCategories(newExpanded);
  };

  const toggleCategorySelection = (category: string) => {
    const newSelected = new Set(selectedCategories);
    if (newSelected.has(category)) {
      newSelected.delete(category);
    } else {
      newSelected.add(category);
    }
    setSelectedCategories(newSelected);
  };

  const toggleSelectAll = () => {
    if (selectedCategories.size === 4) {
      setSelectedCategories(new Set());
    } else {
      setSelectedCategories(new Set(['passphrases', 'dpsk_pools', 'identities', 'identity_groups']));
    }
  };

  const getCategoryLabel = (category: string): string => {
    const labels: Record<string, string> = {
      passphrases: 'DPSK Passphrases',
      dpsk_pools: 'DPSK Pools (Services)',
      identities: 'Identities',
      identity_groups: 'Identity Groups'
    };
    return labels[category] || category;
  };

  const getCategoryIcon = (category: string): string => {
    const icons: Record<string, string> = {
      passphrases: '🔑',
      dpsk_pools: '📦',
      identities: '👤',
      identity_groups: '👥'
    };
    return icons[category] || '📋';
  };

  const getItemDisplayName = (item: ResourceItem, category: string): string => {
    if (category === 'passphrases') {
      return item.username || item.id;
    }
    return item.name || item.id;
  };

  const getTotalSelected = (): number => {
    if (!inventory) return 0;
    let total = 0;
    for (const category of selectedCategories) {
      total += inventory[category as keyof ResourceInventory]?.length || 0;
    }
    return total;
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center p-4">
      <div className="bg-gradient-to-br from-gray-900 to-gray-800 rounded-lg shadow-2xl max-w-3xl w-full max-h-[90vh] overflow-hidden border-4 border-red-600">
        {/* Header */}
        <div className="bg-gradient-to-r from-red-900 to-orange-900 p-6 border-b-4 border-red-600">
          <div className="flex items-center gap-3">
            <span className="text-4xl animate-pulse">☢️</span>
            <div>
              <h2 className="text-2xl font-bold text-white">Nuclear Cleanup Preview</h2>
              <p className="text-red-200 text-sm mt-1">
                Review resources that will be PERMANENTLY DELETED from {venueName}
              </p>
            </div>
          </div>
        </div>

        {/* Body */}
        <div className="p-6 overflow-y-auto max-h-[60vh]">
          {loading && (
            <div className="flex flex-col items-center justify-center py-12">
              <Loader className="animate-spin text-orange-500 mb-4" size={48} />
              <p className="text-gray-300 text-lg">Scanning venue for DPSK resources...</p>
            </div>
          )}

          {error && (
            <div className="bg-red-900/50 border-2 border-red-600 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="text-red-400 flex-shrink-0 mt-1" size={24} />
                <div>
                  <h3 className="text-red-200 font-semibold mb-1">Error Loading Preview</h3>
                  <p className="text-red-300 text-sm">{error}</p>
                </div>
              </div>
            </div>
          )}

          {!loading && !error && inventory && (
            <>
              {/* Select All */}
              <div className="mb-6 p-4 bg-gray-800/50 rounded-lg border-2 border-orange-600">
                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedCategories.size === 4}
                    onChange={toggleSelectAll}
                    className="w-5 h-5 rounded border-gray-600 bg-gray-700 text-orange-600 focus:ring-orange-500"
                  />
                  <span className="text-white font-semibold text-lg">
                    Select All Categories ({getTotalSelected()} total resources)
                  </span>
                </label>
              </div>

              {/* Resource Categories */}
              <div className="space-y-3">
                {(['passphrases', 'dpsk_pools', 'identities', 'identity_groups'] as const).map((category) => {
                  const items = inventory[category] || [];
                  const isExpanded = expandedCategories.has(category);
                  const isSelected = selectedCategories.has(category);

                  return (
                    <div
                      key={category}
                      className={`border-2 rounded-lg overflow-hidden transition-all ${
                        isSelected
                          ? 'border-red-600 bg-red-900/20'
                          : 'border-gray-700 bg-gray-800/30'
                      }`}
                    >
                      {/* Category Header */}
                      <div className="p-4">
                        <div className="flex items-center gap-3">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleCategorySelection(category)}
                            className="w-5 h-5 rounded border-gray-600 bg-gray-700 text-red-600 focus:ring-red-500"
                          />
                          <button
                            onClick={() => toggleCategory(category)}
                            className="flex items-center gap-2 flex-1 text-left hover:text-orange-400 transition-colors"
                          >
                            {isExpanded ? (
                              <ChevronDown className="text-orange-500" size={20} />
                            ) : (
                              <ChevronRight className="text-gray-500" size={20} />
                            )}
                            <span className="text-2xl">{getCategoryIcon(category)}</span>
                            <span className="text-white font-semibold">
                              {getCategoryLabel(category)}
                            </span>
                            <span className={`ml-auto px-3 py-1 rounded-full text-sm font-medium ${
                              items.length > 0
                                ? 'bg-red-600 text-white'
                                : 'bg-gray-700 text-gray-400'
                            }`}>
                              {items.length}
                            </span>
                          </button>
                        </div>
                      </div>

                      {/* Expanded Items */}
                      {isExpanded && items.length > 0 && (
                        <div className="border-t-2 border-gray-700 bg-gray-900/50 p-4 max-h-64 overflow-y-auto">
                          <ul className="space-y-2">
                            {items.map((item, index) => (
                              <li
                                key={item.id || index}
                                className="text-gray-300 text-sm pl-4 py-1 border-l-2 border-gray-700 hover:border-orange-600 hover:text-white transition-colors"
                              >
                                {getItemDisplayName(item, category)}
                                {category === 'passphrases' && item.pool_id && (
                                  <span className="text-gray-500 text-xs ml-2">
                                    (Pool: {item.pool_id})
                                  </span>
                                )}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {isExpanded && items.length === 0 && (
                        <div className="border-t-2 border-gray-700 bg-gray-900/50 p-4">
                          <p className="text-gray-500 text-sm text-center">No items found</p>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Warning Box */}
              {getTotalSelected() > 0 && (
                <div className="mt-6 p-4 bg-red-900/30 border-2 border-red-600 rounded-lg">
                  <div className="flex items-start gap-3">
                    <AlertTriangle className="text-red-400 flex-shrink-0 mt-1" size={24} />
                    <div className="text-red-200 text-sm">
                      <p className="font-semibold mb-1">Warning: This action cannot be undone!</p>
                      <p>
                        You are about to permanently delete <span className="font-bold text-red-100">{getTotalSelected()} resources</span> from {venueName}.
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="bg-gray-900 p-6 border-t-2 border-gray-700 flex gap-4 justify-end">
          <button
            onClick={onClose}
            className="px-6 py-3 bg-gray-700 hover:bg-gray-600 text-white rounded-lg font-semibold transition-colors"
          >
            Cancel
          </button>
          {!loading && !error && inventory && getTotalSelected() > 0 && (
            <button
              onClick={onConfirm}
              className="px-6 py-3 bg-gradient-to-r from-red-600 to-orange-600 hover:from-red-700 hover:to-orange-700 text-white rounded-lg font-semibold transition-all shadow-lg hover:shadow-red-500/50 flex items-center gap-2"
            >
              <span className="text-xl">☢️</span>
              Proceed with Nuclear Cleanup
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default NuclearCleanupModal;
