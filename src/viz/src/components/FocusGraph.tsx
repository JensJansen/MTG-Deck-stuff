import { useEffect, useRef } from 'react';
import Cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { COLOR_HEX } from '../constants';
import type { CardNode, FocusGraphData, ColorCat } from '../types';
import './FocusGraph.css';

try { Cytoscape.use(fcose); } catch { /* already registered */ }

interface Props {
  data: FocusGraphData;
  allNodes: CardNode[];
  onNodeClick: (idx: number) => void;
}

export function FocusGraph({ data, allNodes, onNodeClick }: Props) {
  const divRef   = useRef<HTMLDivElement>(null);
  const clickRef = useRef(onNodeClick);
  clickRef.current = onNodeClick;

  useEffect(() => {
    const div = divRef.current;
    if (!div) return;

    const maxDeckCount = Math.max(...allNodes.map(n => n.deck_count), 1);

    const cyNodes = data.nodes.map(({ id, depth }) => {
      const card = allNodes[id];
      const size =
        depth === 0 ? 28 :
        depth === 1 ? 8 + 14 * Math.sqrt(card.deck_count / maxDeckCount) :
        Math.max(5, 4 + 8 * Math.sqrt(card.deck_count / maxDeckCount));

      return { data: { id: String(id), label: card.name, color: COLOR_HEX[card.color_cat as ColorCat] ?? '#9E9E9E', size, depth } };
    });

    const cyEdges = data.edges.map((e, i) => ({
      data: {
        id:          `e${i}`,
        source:      String(e.source),
        target:      String(e.target),
        opacity:     Math.max(0.08, Math.min(0.85, 0.15 + 0.65 * e.jaccard)),
        idealLength: e.idealLength,
      },
    }));

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const stylesheet: any[] = [
      {
        selector: 'node',
        style: {
          'background-color':        'data(color)',
          'width':                   'data(size)',
          'height':                  'data(size)',
          'label':                   'data(label)',
          'font-size':               9,
          'color':                   '#bbbbbb',
          'text-valign':             'bottom',
          'text-halign':             'center',
          'text-margin-y':           3,
          'text-background-color':   '#0d1117',
          'text-background-opacity': 0.65,
          'text-background-padding': '2px',
          'cursor':                  'pointer',
          'overlay-opacity':         0,
        },
      },
      { selector: 'node[depth = 0]', style: { 'border-width': 2.5, 'border-color': '#ffffff', 'font-size': 13, 'font-weight': 'bold', 'color': '#ffffff' } },
      { selector: 'node[depth = 2]', style: { 'font-size': 7, 'color': '#777777' } },
      { selector: 'node:active',     style: { 'overlay-opacity': 0.12 } },
      { selector: 'edge',            style: { 'width': 1, 'line-color': '#ffffff', 'opacity': 'data(opacity)', 'curve-style': 'straight' } },
    ];

    const cy = Cytoscape({ container: div, elements: [...cyNodes, ...cyEdges], style: stylesheet, layout: { name: 'preset' }, autoungrabify: true });

    const layout = cy.layout({
      name: 'fcose', quality: 'proof', animate: false, randomize: true,
      fixedNodeConstraint: [{ nodeId: String(data.focusNodeId), position: { x: 0, y: 0 } }],
      idealEdgeLength: (edge: Cytoscape.EdgeSingular) => (edge.data('idealLength') as number) ?? 100,
      nodeRepulsion: () => 6500, edgeElasticity: () => 0.45, gravity: 0.25, gravityRange: 3.8,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);

    layout.on('layoutstop', () => cy.fit(undefined, 60));
    layout.run();

    cy.on('tap', 'node', (evt: Cytoscape.EventObject) => {
      clickRef.current(parseInt(evt.target.id() as string, 10));
    });

    return () => { cy.destroy(); };
  }, [data, allNodes]);

  return <div ref={divRef} className="focus-graph" />;
}
