import { useEffect, useRef } from 'react';
import Plotly from 'plotly.js-dist-min';
import { COLOR_HEX } from '../constants';
import type { CardNode } from '../types';

interface Props {
  nodes: CardNode[];
  highlightedIndices: number[] | null;
  onNodeClick: (idx: number) => void;
}

export function ScatterPlot({ nodes, highlightedIndices, onNodeClick }: Props) {
  const divRef     = useRef<HTMLDivElement>(null);
  const curNodes   = useRef<CardNode[] | null>(null);
  const onClickRef = useRef(onNodeClick);
  onClickRef.current = onNodeClick;

  // Init or re-init when the dataset (format) changes
  useEffect(() => {
    if (!divRef.current || !nodes.length) return;
    if (curNodes.current === nodes) return;
    const div = divRef.current;

    const maxDeck = Math.max(...nodes.map(n => n.deck_count));

    const trace = {
      type: 'scattergl',
      mode: 'markers',
      x: nodes.map(n => n.x),
      y: nodes.map(n => n.y),
      text: nodes.map(n => n.name),
      customdata: nodes.map(n => [n.inclusion_pct, n.deck_count, n.avg_qty]),
      marker: {
        color:   nodes.map(n => COLOR_HEX[n.color_cat] ?? '#9E9E9E'),
        size:    nodes.map(n => 3 + 9 * Math.sqrt(n.deck_count / maxDeck)),
        opacity: 0.8,
        line: { width: 0 },
      },
      hovertemplate:
        '<b>%{text}</b><br>' +
        'Inclusion: %{customdata[0]}%<br>' +
        'Decks: %{customdata[1]}<br>' +
        'Avg copies: %{customdata[2]}<extra></extra>',
      selected:   { marker: { opacity: 1 } },
      unselected: { marker: { opacity: 0.08 } },
    };

    const layout = {
      paper_bgcolor: '#0d1117',
      plot_bgcolor:  '#0d1117',
      xaxis: { showgrid: false, zeroline: false, showticklabels: false },
      yaxis: { showgrid: false, zeroline: false, showticklabels: false, scaleanchor: 'x' },
      margin: { l: 0, r: 0, t: 0, b: 0 },
      hovermode:  'closest',
      showlegend: false,
      dragmode:   'pan',
      uirevision: 'static',
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    Plotly.purge(div as any);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    Plotly.newPlot(div as any, [trace as any], layout as any, {
      displayModeBar: false,
      scrollZoom: true,
      responsive: true,
    });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (div as any).on('plotly_click', (evt: any) => {
      onClickRef.current(evt.points[0].pointIndex as number);
    });

    curNodes.current = nodes;
  }, [nodes]);

  // Update search highlight independently of position
  useEffect(() => {
    if (!divRef.current || !curNodes.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    Plotly.restyle(divRef.current as any, { selectedpoints: [highlightedIndices] } as any);
  }, [highlightedIndices]);

  return <div ref={divRef} style={{ width: '100%', height: '100%' }} />;
}
