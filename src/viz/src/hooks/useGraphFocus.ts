import { useState, useCallback, useMemo } from 'react';
import { colorFilterPass } from '../constants';
import type { CardNode, EgoPartner, FocusData, FocusGraphData } from '../types';

const DEPTH1_N = 40;
const DEPTH2_N = 15;

const D1_LENGTH_MIN = 80;
const D1_LENGTH_MAX = 300;
const D2_LENGTH     = 80;

// Pure function — called both on initial focus and reactively when the color
// filter changes while already in focus mode.
function buildFocusGraphData(
  nodeIdx:       number,
  focusedName:   string,
  focusData:     FocusData,
  nodes:         CardNode[],
  edges:         [number, number, number][],
  ego:           Record<string, EgoPartner[]>,
  nameToIdx:     Record<string, number>,
  selectedColors: Set<string>,
  colorMode:     'including' | 'exactly',
): FocusGraphData {
  // ----------------------------------------------------------------
  // Depth-1: top DEPTH1_N direct partners by co-occurrence count,
  // filtered by the active color selection.
  // The focus card itself (depth-0) always renders regardless of filter.
  // ----------------------------------------------------------------
  const allPartners = focusData[focusedName] ?? [];
  const depth1Map = new Map<number, number>(); // idx → co-occurrence count
  const depth1Set = new Set<number>();
  for (const [name, count] of allPartners.map(p => [p[0], p[1]] as [string, number])) {
    if (depth1Map.size >= DEPTH1_N) break;
    const i = nameToIdx[name];
    if (i !== undefined && colorFilterPass(nodes[i], selectedColors, colorMode)) {
      depth1Map.set(i, count);
      depth1Set.add(i);
    }
  }

  // ----------------------------------------------------------------
  // Depth-2: top DEPTH2_N ego partners per depth-1 card, also filtered.
  // ----------------------------------------------------------------
  const depth2Parent = new Map<number, number>(); // d2 idx → d1 parent idx
  for (const d1Idx of depth1Set) {
    const cardEgo = ego[nodes[d1Idx].name] ?? [];
    let added = 0;
    for (const ep of cardEgo) {
      if (added >= DEPTH2_N) break;
      const i = nameToIdx[ep.n];
      if (
        i !== undefined &&
        i !== nodeIdx &&
        !depth1Set.has(i) &&
        !depth2Parent.has(i) &&
        colorFilterPass(nodes[i], selectedColors, colorMode)
      ) {
        depth2Parent.set(i, d1Idx);
        added++;
      }
    }
  }
  const depth2Set    = new Set(depth2Parent.keys());
  const nonFocusNodes = new Set([...depth1Set, ...depth2Set]);

  // ----------------------------------------------------------------
  // Log-normalise co-occurrence counts for edge length encoding.
  // ----------------------------------------------------------------
  const counts   = [...depth1Map.values()];
  const maxCount = Math.max(...counts, 1);
  const minCount = Math.min(...counts, maxCount);
  const logMax   = Math.log(maxCount);
  const logMin   = Math.log(Math.max(minCount, 1));
  const logRange = logMax > logMin ? logMax - logMin : 1;

  // ----------------------------------------------------------------
  // Build graph nodes
  // ----------------------------------------------------------------
  const graphNodes: FocusGraphData['nodes'] = [{ id: nodeIdx, depth: 0 }];
  for (const [idx] of depth1Map) graphNodes.push({ id: idx, depth: 1 });
  for (const idx of depth2Set)  graphNodes.push({ id: idx, depth: 2 });

  // ----------------------------------------------------------------
  // Build display edges
  // ----------------------------------------------------------------
  const displayEdges: FocusGraphData['edges'] = [];

  for (const [idx, count] of depth1Map) {
    const t = (Math.log(count) - logMin) / logRange;
    displayEdges.push({
      source:      nodeIdx,
      target:      idx,
      jaccard:     0.3 + 0.7 * t,
      idealLength: D1_LENGTH_MIN + (D1_LENGTH_MAX - D1_LENGTH_MIN) * (1 - t),
    });
  }

  const candidates: Array<{ source: number; target: number; jaccard: number }> = [];
  for (const [a, b, distInt] of edges) {
    if (nonFocusNodes.has(a) && nonFocusNodes.has(b)) {
      candidates.push({ source: a, target: b, jaccard: 1 - distInt / 100 });
    }
  }
  candidates.sort((x, y) => y.jaccard - x.jaccard);

  const ufParent = new Map<number, number>();
  function ufFind(x: number): number {
    if (!ufParent.has(x)) ufParent.set(x, x);
    const p = ufParent.get(x)!;
    if (p !== x) { const r = ufFind(p); ufParent.set(x, r); return r; }
    return x;
  }
  function ufUnion(x: number, y: number): boolean {
    const px = ufFind(x), py = ufFind(y);
    if (px === py) return false;
    ufParent.set(px, py);
    return true;
  }

  for (const idx of depth1Set) ufUnion(nodeIdx, idx);
  for (const c of candidates) {
    if (ufUnion(c.source, c.target)) {
      displayEdges.push({ ...c, idealLength: D2_LENGTH });
    }
  }

  return { focusNodeId: nodeIdx, nodes: graphNodes, edges: displayEdges };
}

export function useGraphFocus(
  nodes:          CardNode[],
  edges:          [number, number, number][],
  format:         string | null,
  ego:            Record<string, EgoPartner[]>,
  selectedColors: Set<string>,
  colorMode:      'including' | 'exactly',
) {
  const [focusedIdx,      setFocusedIdx]      = useState<number | null>(null);
  const [focusData,       setFocusData]       = useState<FocusData | null>(null);
  const [focusDataFormat, setFocusDataFormat] = useState<string | null>(null);

  const nameToIdx = useMemo<Record<string, number>>(() => {
    const m: Record<string, number> = {};
    nodes.forEach((n, i) => { m[n.name] = i; });
    return m;
  }, [nodes]);

  // Recomputes whenever focusedIdx, filter, or the underlying data changes.
  const focusGraphData = useMemo<FocusGraphData | null>(() => {
    if (focusedIdx === null || !focusData) return null;
    return buildFocusGraphData(
      focusedIdx,
      nodes[focusedIdx].name,
      focusData,
      nodes,
      edges,
      ego,
      nameToIdx,
      selectedColors,
      colorMode,
    );
  }, [focusedIdx, focusData, nodes, edges, ego, nameToIdx, selectedColors, colorMode]);

  const applyFocus = useCallback(async (nodeIdx: number) => {
    if (!format) return;

    let fd = focusData;
    if (!fd || focusDataFormat !== format) {
      const res = await fetch(`/data/${format}.focus.json`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      fd = await res.json() as FocusData;
      setFocusData(fd);
      setFocusDataFormat(format);
    }

    setFocusedIdx(nodeIdx);
  }, [focusData, focusDataFormat, format]);

  const resetFocus = useCallback(() => {
    setFocusedIdx(null);
  }, []);

  return {
    focusedNode:    focusedIdx !== null ? nodes[focusedIdx] : null,
    isFocused:      focusedIdx !== null,
    focusGraphData,
    applyFocus,
    resetFocus,
  };
}
