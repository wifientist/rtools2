/**
 * WorkflowGraph - DAG visualization of workflow phases
 *
 * Renders workflow phases as nodes in a left-to-right DAG layout
 * with dependency edges. Supports static (pre-execution) and live
 * (during execution) modes.
 *
 * Usage:
 *   <WorkflowGraph workflowName="per_unit_psk" />
 *   <WorkflowGraph workflowName="per_unit_dpsk" liveJobId="abc-123" />
 */

import { useState, useEffect, useCallback, useRef } from 'react';

const API_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// ==================== Types ====================

interface GraphNode {
  id: string;
  type: string;
  data: {
    label: string;
    description?: string;
    per_unit: boolean;
    critical: boolean;
    api_calls_per_unit?: number | string;
    inputs?: string[];
    outputs?: string[];
  };
  position: { x: number; y: number };
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
}

interface GraphData {
  workflow_name: string;
  description?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  levels: Record<string, string[]>;
  phase_count?: number;
}

interface WorkflowGraphProps {
  /** Workflow name to fetch the static graph for */
  workflowName: string;
  /** Optional: live job ID for status overlay */
  liveJobId?: string;
  /** Height of the graph container */
  height?: number;
  /** Show phase details on hover */
  showDetails?: boolean;
  /** Compact mode (smaller nodes) */
  compact?: boolean;
}

// ==================== Constants ====================

const NODE_WIDTH = 180;
const NODE_HEIGHT = 60;
const NODE_SPACING_X = 240;
const NODE_SPACING_Y = 90;
const PADDING = 40;

// ==================== Component ====================

const WorkflowGraph = ({
  workflowName,
  liveJobId,
  height = 400,
  showDetails = true,
  compact = false,
}: WorkflowGraphProps) => {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const fetchGraph = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const url = liveJobId
        ? `${API_URL}/per-unit-ssid/v2/${liveJobId}/graph`
        : `${API_URL}/workflows/v2/${workflowName}/graph`;

      const response = await fetch(url, { credentials: 'include' });

      if (!response.ok) {
        throw new Error(`Failed to fetch graph: ${response.status}`);
      }

      const data = await response.json();
      setGraphData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load graph');
    } finally {
      setLoading(false);
    }
  }, [workflowName, liveJobId]);

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  if (loading) {
    return (
      <div
        className="flex items-center justify-center bg-gray-50 rounded-lg border"
        style={{ height }}
      >
        <div className="text-gray-400 text-sm">Loading workflow graph...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="flex items-center justify-center bg-red-50 rounded-lg border border-red-200"
        style={{ height }}
      >
        <div className="text-red-500 text-sm">{error}</div>
      </div>
    );
  }

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <div
        className="flex items-center justify-center bg-gray-50 rounded-lg border"
        style={{ height }}
      >
        <div className="text-gray-400 text-sm">No phases to display</div>
      </div>
    );
  }

  // Compute layout dimensions
  const nodes = graphData.nodes;
  const edges = graphData.edges;

  const maxX = Math.max(...nodes.map(n => n.position.x)) + NODE_WIDTH + PADDING * 2;
  const maxY = Math.max(...nodes.map(n => n.position.y)) + NODE_HEIGHT + PADDING * 2;
  const svgWidth = Math.max(maxX, 600);
  const svgHeight = Math.max(maxY, height);

  // Build node position map for edge routing
  const nodePositions: Record<string, { x: number; y: number }> = {};
  for (const node of nodes) {
    nodePositions[node.id] = {
      x: node.position.x + PADDING,
      y: node.position.y + PADDING,
    };
  }

  return (
    <div className="bg-gray-50 rounded-lg border overflow-auto" style={{ height }}>
      {/* Header */}
      <div className="px-3 py-2 bg-gray-100 border-b flex items-center justify-between">
        <div className="text-xs font-medium text-gray-600">
          {graphData.description || graphData.workflow_name}
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <span className="w-3 h-3 rounded bg-blue-100 border border-blue-300 inline-block" />
            Per-unit
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-3 rounded bg-gray-100 border border-gray-300 inline-block" />
            Global
          </span>
          <span className="text-gray-400">
            {graphData.phase_count || nodes.length} phases
          </span>
        </div>
      </div>

      {/* SVG Graph */}
      <svg
        ref={svgRef}
        width={svgWidth}
        height={svgHeight}
        className="select-none"
      >
        <defs>
          <marker
            id="arrowhead"
            markerWidth="8"
            markerHeight="6"
            refX="8"
            refY="3"
            orient="auto"
          >
            <polygon points="0 0, 8 3, 0 6" fill="#94a3b8" />
          </marker>
        </defs>

        {/* Edges */}
        {edges.map(edge => {
          const source = nodePositions[edge.source];
          const target = nodePositions[edge.target];
          if (!source || !target) return null;

          const x1 = source.x + NODE_WIDTH;
          const y1 = source.y + NODE_HEIGHT / 2;
          const x2 = target.x;
          const y2 = target.y + NODE_HEIGHT / 2;

          // Bezier curve for smooth edges
          const midX = (x1 + x2) / 2;

          return (
            <path
              key={edge.id}
              d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
              fill="none"
              stroke="#cbd5e1"
              strokeWidth={1.5}
              markerEnd="url(#arrowhead)"
            />
          );
        })}

        {/* Nodes */}
        {nodes.map(node => {
          const x = node.position.x + PADDING;
          const y = node.position.y + PADDING;
          const isPerUnit = node.data.per_unit;
          const isCritical = node.data.critical;
          const isHovered = hoveredNode === node.id;

          const bgClass = isPerUnit ? '#dbeafe' : '#f3f4f6';
          const borderColor = isHovered
            ? '#3b82f6'
            : isPerUnit
            ? '#93c5fd'
            : '#d1d5db';
          const strokeWidth = isHovered ? 2 : 1;

          return (
            <g
              key={node.id}
              onMouseEnter={() => setHoveredNode(node.id)}
              onMouseLeave={() => setHoveredNode(null)}
              style={{ cursor: 'pointer' }}
            >
              {/* Node background */}
              <rect
                x={x}
                y={y}
                width={NODE_WIDTH}
                height={NODE_HEIGHT}
                rx={6}
                ry={6}
                fill={bgClass}
                stroke={borderColor}
                strokeWidth={strokeWidth}
              />

              {/* Critical indicator */}
              {!isCritical && (
                <rect
                  x={x + NODE_WIDTH - 24}
                  y={y + 4}
                  width={18}
                  height={14}
                  rx={3}
                  fill="#fef3c7"
                  stroke="#fbbf24"
                  strokeWidth={0.5}
                />
              )}
              {!isCritical && (
                <text
                  x={x + NODE_WIDTH - 15}
                  y={y + 14}
                  textAnchor="middle"
                  fontSize={8}
                  fill="#92400e"
                >
                  opt
                </text>
              )}

              {/* Per-unit badge */}
              {isPerUnit && (
                <text
                  x={x + 8}
                  y={y + 14}
                  fontSize={8}
                  fill="#1d4ed8"
                  fontWeight="500"
                >
                  per-unit
                </text>
              )}

              {/* Phase name */}
              <text
                x={x + NODE_WIDTH / 2}
                y={y + (isPerUnit ? 38 : 34)}
                textAnchor="middle"
                fontSize={compact ? 10 : 11}
                fontWeight="600"
                fill="#1e293b"
              >
                {node.data.label.length > 22
                  ? node.data.label.substring(0, 20) + '...'
                  : node.data.label}
              </text>

              {/* API calls indicator */}
              {node.data.api_calls_per_unit !== undefined && !isPerUnit && (
                <text
                  x={x + NODE_WIDTH / 2}
                  y={y + NODE_HEIGHT - 8}
                  textAnchor="middle"
                  fontSize={8}
                  fill="#6b7280"
                >
                  global
                </text>
              )}

              {/* Tooltip on hover */}
              {isHovered && showDetails && node.data.description && (
                <g>
                  <rect
                    x={x}
                    y={y + NODE_HEIGHT + 4}
                    width={Math.max(NODE_WIDTH, node.data.description.length * 5.5)}
                    height={24}
                    rx={4}
                    fill="#1e293b"
                    opacity={0.9}
                  />
                  <text
                    x={x + 6}
                    y={y + NODE_HEIGHT + 20}
                    fontSize={9}
                    fill="white"
                  >
                    {node.data.description.substring(0, 60)}
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
};

export default WorkflowGraph;
