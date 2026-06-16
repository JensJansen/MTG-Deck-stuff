import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { COLOR_HEX } from '../constants';
import type { CardNode, EgoPartner } from '../types';
import './EgoNetwork.css';

interface Props {
  center: CardNode;
  partners: EgoPartner[];
  nameIndex: Record<string, number>;
  nodes: CardNode[];
  onNodeClick: (name: string) => void;
}

interface SimNode extends d3.SimulationNodeDatum {
  id: string;
  isCenter: boolean;
  lift?: number;
  cooccur?: number;
  jaccard?: number;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  lift: number;
  cooccur: number;
}

export function EgoNetwork({ center, partners, nameIndex, nodes, onNodeClick }: Props) {
  const svgRef   = useRef<SVGSVGElement>(null);
  const tipRef   = useRef<HTMLDivElement>(null);
  const clickRef = useRef(onNodeClick);
  clickRef.current = onNodeClick;

  useEffect(() => {
    const svg = svgRef.current;
    const tip = tipRef.current;
    if (!svg) return;

    const W = svg.clientWidth  || 380;
    const H = svg.clientHeight || 400;

    const sel = d3.select(svg).attr('width', W).attr('height', H);
    sel.selectAll('*').remove();

    if (!partners.length) {
      sel.append('text')
        .attr('x', W / 2).attr('y', H / 2)
        .attr('text-anchor', 'middle').attr('fill', '#444').attr('font-size', '13px')
        .text('No co-occurrence data.');
      return;
    }

    const simNodes: SimNode[] = [
      { id: center.name, isCenter: true },
      ...partners.map(p => ({ id: p.n, isCenter: false, lift: p.l, cooccur: p.c, jaccard: p.j })),
    ];
    const simLinks: SimLink[] = partners.map(p => ({ source: center.name, target: p.n, lift: p.l, cooccur: p.c }));

    const maxCooccur = d3.max(simLinks, d => d.cooccur) ?? 1;
    const maxLift    = d3.max(simLinks, d => d.lift)    ?? 1;
    const strokeW    = d3.scaleLinear().domain([0, maxCooccur]).range([0.5, 3.5]);
    const edgeOpac   = d3.scaleLinear().domain([0, maxLift]).range([0.2, 0.85]).clamp(true);

    const sim = d3.forceSimulation<SimNode>(simNodes)
      .force('link',    d3.forceLink<SimNode, SimLink>(simLinks).id(d => d.id).distance(70).strength(0.6))
      .force('charge',  d3.forceManyBody().strength(-160))
      .force('center',  d3.forceCenter(W / 2, H / 2))
      .force('collide', d3.forceCollide(20));

    const g    = sel.append('g');
    const link = g.append('g').selectAll<SVGLineElement, SimLink>('line')
      .data(simLinks).join('line')
      .attr('stroke', '#30363d')
      .attr('stroke-width', d => strokeW(d.cooccur))
      .attr('stroke-opacity', d => edgeOpac(d.lift));

    const node = g.append('g').selectAll<SVGGElement, SimNode>('g')
      .data(simNodes).join('g')
      .style('cursor', 'pointer')
      .call(
        d3.drag<SVGGElement, SimNode>()
          .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
          .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
          .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
      );

    node.append('circle')
      .attr('r', d => d.isCenter ? 13 : 8)
      .attr('fill', d => { const idx = nameIndex[d.id]; return idx !== undefined ? (COLOR_HEX[nodes[idx].color_cat] ?? '#555') : '#555'; })
      .attr('stroke', d => d.isCenter ? '#e6edf3' : 'transparent')
      .attr('stroke-width', 2);

    node.append('text')
      .attr('dy', d => d.isCenter ? -17 : -11)
      .attr('text-anchor', 'middle')
      .attr('font-size', d => d.isCenter ? '11px' : '9px')
      .attr('fill', d => d.isCenter ? '#e6edf3' : '#8b949e')
      .attr('pointer-events', 'none')
      .text(d => d.id.length > 22 ? d.id.slice(0, 20) + '...' : d.id);

    node
      .on('mousemove', (e, d) => {
        if (d.isCenter || !tip) return;
        tip.style.display = 'block';
        tip.style.left = `${e.clientX + 12}px`;
        tip.style.top  = `${e.clientY - 8}px`;
        tip.innerHTML = `<b>${d.id}</b><br>Co-occurrences: ${d.cooccur?.toLocaleString()}<br>Lift: ${d.lift} &nbsp;·&nbsp; Jaccard: ${d.jaccard}`;
      })
      .on('mouseleave', () => { if (tip) tip.style.display = 'none'; })
      .on('click', (e, d) => {
        if (d.isCenter) return;
        e.stopPropagation();
        clickRef.current(d.id);
      });

    sim.on('tick', () => {
      link
        .attr('x1', d => (d.source as SimNode).x ?? 0).attr('y1', d => (d.source as SimNode).y ?? 0)
        .attr('x2', d => (d.target as SimNode).x ?? 0).attr('y2', d => (d.target as SimNode).y ?? 0);
      node.attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    return () => { sim.stop(); };
  }, [center, partners, nameIndex, nodes]);

  return (
    <div className="ego-network">
      <svg ref={svgRef} />
      <div ref={tipRef} className="ego-network__tooltip" />
    </div>
  );
}
