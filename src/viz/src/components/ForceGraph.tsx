import { useEffect, useRef, useState } from 'react';
import Cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { COLOR_HEX, colorFilterPass } from '../constants';
import type { CardNode, ColorCat } from '../types';
import './ForceGraph.css';

try { Cytoscape.use(fcose); } catch { /* already registered by FocusGraph */ }

interface Props {
  nodes:              CardNode[];
  edges:              [number, number, number][];
  highlightedIndices: number[] | null;
  onNodeClick:        (idx: number) => void;
  selectedColors:     Set<string>;
  colorMode:          'including' | 'exactly';
}

const PER_NODE_EDGE_CAP      = 5;
const MAX_EDGES              = 3000;
const EDGE_REVEAL_THRESHOLD  = 0.15;
const LABEL_PX_THRESHOLD     = 20;

function edgeWidth(jaccard: number):   number { return 0.5 + 2.5 * jaccard; }
function edgeOpacity(jaccard: number): number { return Math.max(0.06, Math.min(0.55, 0.1 + 0.45 * jaccard)); }
function nodeSize(deckCount: number, maxDeck: number): number { return 6 + 20 * Math.sqrt(deckCount / maxDeck); }

export function ForceGraph({ nodes, edges, highlightedIndices, onNodeClick, selectedColors, colorMode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef        = useRef<Cytoscape.Core | null>(null);
  const clickRef     = useRef(onNodeClick);
  clickRef.current   = onNodeClick;

  const [topN,       setTopN]       = useState(300);
  const [minJaccard, setMinJaccard] = useState(0.2);
  const [committed,  setCommitted]  = useState({ topN: 300, minJaccard: 0.2 });
  const [showEdges,  setShowEdges]  = useState(true);

  const showEdgesRef     = useRef(true);
  showEdgesRef.current   = showEdges;
  const updateEdgeVisRef = useRef<() => void>(() => {});

  // ── Build + render graph ──────────────────────────────────────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !nodes.length) return;

    // 1. Apply color filter, then take top-N by deck_count
    const ranked = nodes
      .map((n, i) => ({ n, i }))
      .filter(({ n }) => colorFilterPass(n, selectedColors, colorMode))
      .sort((a, b) => b.n.deck_count - a.n.deck_count)
      .slice(0, committed.topN);

    const includedSet  = new Set(ranked.map(x => x.i));
    const maxDistInt   = Math.round((1 - committed.minJaccard) * 100);

    // 2. Threshold filter
    const filtered = edges.filter(
      ([a, b, dist]) => includedSet.has(a) && includedSet.has(b) && dist <= maxDistInt,
    );

    // 3. Per-node edge cap — strongest first
    const byJaccard  = [...filtered].sort((x, y) => x[2] - y[2]);
    const nodeDegree = new Map<number, number>();
    const capped: typeof filtered = [];
    for (const edge of byJaccard) {
      const [a, b] = edge;
      const da = nodeDegree.get(a) ?? 0;
      const db = nodeDegree.get(b) ?? 0;
      if (da < PER_NODE_EDGE_CAP && db < PER_NODE_EDGE_CAP) {
        capped.push(edge);
        nodeDegree.set(a, da + 1);
        nodeDegree.set(b, db + 1);
      }
    }
    const kept = capped.length > MAX_EDGES ? capped.slice(0, MAX_EDGES) : capped;

    // 4. Exclude isolated nodes
    const connectedSet = new Set<number>();
    for (const [a, b] of kept) { connectedSet.add(a); connectedSet.add(b); }
    const displayRanked = ranked.filter(({ i }) => connectedSet.has(i));
    const maxDeck       = displayRanked[0]?.n.deck_count ?? 1;

    // 5. Build Cytoscape elements
    const cyNodes = displayRanked.map(({ n, i }) => ({
      data: { id: String(i), label: n.name, color: COLOR_HEX[n.color_cat as ColorCat] ?? '#9E9E9E', size: nodeSize(n.deck_count, maxDeck) },
    }));

    const cyEdges = kept.map(([a, b, distInt], idx) => {
      const jac = 1 - distInt / 100;
      return { data: { id: `e${idx}`, source: String(a), target: String(b), width: edgeWidth(jac), opacity: edgeOpacity(jac), jaccard: jac } };
    });

    // 6. Stylesheet
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const stylesheet: any[] = [
      { selector: 'node', style: { 'shape': 'ellipse', 'background-color': 'data(color)', 'width': 'data(size)', 'height': 'data(size)', 'label': '', 'cursor': 'pointer', 'overlay-opacity': 0, 'text-background-opacity': 0, 'opacity': 0.85 } },
      { selector: 'node:active', style: { 'overlay-opacity': 0 } },
      { selector: 'node:hover', style: { 'label': 'data(label)', 'font-size': 11, 'color': '#e0e0e0', 'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': 4, 'opacity': 1 } },
      { selector: 'node.labeled', style: { 'label': 'data(label)', 'font-size': 9, 'color': '#aaaaaa', 'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': 4 } },
      { selector: 'node.highlighted', style: { 'label': 'data(label)', 'font-size': 10, 'color': '#ffffff', 'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': 4, 'border-width': 2, 'border-color': '#ffffff', 'opacity': 1 } },
      { selector: 'node.dimmed', style: { 'opacity': 0.07, 'label': '' } },
      { selector: 'edge', style: { 'width': 'data(width)', 'line-color': '#ffffff', 'opacity': 'data(opacity)', 'curve-style': 'straight', 'overlay-opacity': 0 } },
      { selector: 'edge.dimmed', style: { 'opacity': 0.02 } },
    ];

    // 7. Destroy previous instance
    if (cyRef.current) { cyRef.current.destroy(); cyRef.current = null; }

    const cy = Cytoscape({ container, elements: [...cyNodes, ...cyEdges], style: stylesheet, layout: { name: 'preset' }, autoungrabify: true });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layout = cy.layout({
      name: 'fcose', quality: 'default', animate: false, randomize: true,
      idealEdgeLength: (edge: Cytoscape.EdgeSingular) => { const jac = edge.data('jaccard') as number; return 40 + 140 * (1 - jac); },
      nodeRepulsion: () => 4500, edgeElasticity: () => 0.45, gravity: 0.25, gravityRange: 3.5,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);

    function updateLabels() {
      const z = cy.zoom();
      cy.batch(() => {
        cy.nodes().forEach(n => {
          if ((n.data('size') as number) * z >= LABEL_PX_THRESHOLD) n.addClass('labeled');
          else                                                        n.removeClass('labeled');
        });
      });
    }

    function updateEdgeVisibility() {
      if (!showEdgesRef.current) { cy.edges().style('display', 'none'); return; }
      const z = cy.zoom();
      cy.batch(() => {
        cy.edges().forEach(e => {
          e.style('display', (e.data('jaccard') as number) * z >= EDGE_REVEAL_THRESHOLD ? 'element' : 'none');
        });
      });
    }
    updateEdgeVisRef.current = updateEdgeVisibility;

    layout.on('layoutstop', () => { cy.fit(undefined, 40); updateLabels(); updateEdgeVisibility(); });
    layout.run();

    cy.on('zoom', () => { updateLabels(); updateEdgeVisibility(); });
    cy.on('tap', 'node', (evt: Cytoscape.EventObject) => { clickRef.current(parseInt(evt.target.id() as string, 10)); });

    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
  }, [nodes, edges, committed, selectedColors, colorMode]);

  // ── Search highlights ─────────────────────────────────────────────────────
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    if (!highlightedIndices || highlightedIndices.length === 0) {
      cy.elements().removeClass('highlighted dimmed');
      updateEdgeVisRef.current();
      return;
    }

    const hitSet = new Set(highlightedIndices.map(String));
    cy.batch(() => {
      cy.nodes().forEach(n => {
        if (hitSet.has(n.id())) n.addClass('highlighted').removeClass('dimmed');
        else                    n.addClass('dimmed').removeClass('highlighted');
      });
      cy.edges().forEach(e => {
        const connected = hitSet.has(e.source().id()) || hitSet.has(e.target().id());
        if (connected) e.removeClass('dimmed'); else e.addClass('dimmed');
      });
    });
    updateEdgeVisRef.current();
  }, [highlightedIndices]);

  // ── Show/hide edges toggle ────────────────────────────────────────────────
  useEffect(() => { updateEdgeVisRef.current(); }, [showEdges]);

  function commitSliders() { setCommitted({ topN, minJaccard }); }

  return (
    <div className="force-graph">
      <div ref={containerRef} className="force-graph__canvas" />

      <div className="force-graph__controls">
        <div className="force-graph__slider-group">
          <div className="force-graph__slider-row">
            <span>Top cards</span>
            <strong className="force-graph__slider-value">{topN}</strong>
          </div>
          <input className="force-graph__slider" type="range" min={100} max={2000} step={50} value={topN}
            onChange={e => setTopN(+e.target.value)} onMouseUp={commitSliders} onTouchEnd={commitSliders} />
        </div>

        <label className="force-graph__checkbox-label">
          <input type="checkbox" checked={showEdges} onChange={e => setShowEdges(e.target.checked)} />
          Show edges
        </label>

        <div>
          <div className="force-graph__slider-row">
            <span>Min strength</span>
            <strong className="force-graph__slider-value">{minJaccard.toFixed(2)}</strong>
          </div>
          <input className="force-graph__slider" type="range" min={0.05} max={0.5} step={0.05} value={minJaccard}
            onChange={e => setMinJaccard(+e.target.value)} onMouseUp={commitSliders} onTouchEnd={commitSliders} />
        </div>
      </div>
    </div>
  );
}
