import { useMemo, useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { COLOR_HEX, COLOR_LABEL, COLOR_BITS, COLOR_ORDER } from '../constants';
import type { CardNode, ColorCat, ArchetypeData, ArchetypeNode } from '../types';
import './ColorsView.css';

const MONO   = ['W', 'U', 'B', 'R', 'G'] as const;
const ALL_CATS = COLOR_ORDER;
type MonoColor = typeof MONO[number];

// ── Data shapes ───────────────────────────────────────────────────────────────

interface MetaEntry  { cat: string; count: number; pct: number; }
interface ColorStat  { color: MonoColor; cardCount: number; avgCardsPerDeck: number; topCards: CardNode[]; }
interface InclDist   { cat: string; min: number; q1: number; median: number; q3: number; max: number; count: number; }

// ── Derived stats ─────────────────────────────────────────────────────────────

function useColorStats(nodes: CardNode[]) {
  return useMemo(() => {
    // 1. Meta share weighted by deck_count
    const rawTotals: Record<string, number> = {};
    for (const n of nodes) rawTotals[n.color_cat] = (rawTotals[n.color_cat] ?? 0) + n.deck_count;
    const grandTotal = Object.values(rawTotals).reduce((a, b) => a + b, 0) || 1;
    const metaShare: MetaEntry[] = ALL_CATS
      .filter(c => (rawTotals[c] ?? 0) > 0)
      .map(c => ({ cat: c, count: rawTotals[c] ?? 0, pct: (rawTotals[c] ?? 0) / grandTotal }))
      .sort((a, b) => b.count - a.count);

    // 2. Pair co-occurrence matrix (WUBRG × WUBRG)
    const matrix: number[][] = Array.from({ length: 5 }, () => new Array(5).fill(0));
    for (const n of nodes) {
      for (let i = 0; i < 5; i++) {
        if (!(n.color_mask & (COLOR_BITS[MONO[i]] ?? 0))) continue;
        for (let j = i; j < 5; j++) {
          if (!(n.color_mask & (COLOR_BITS[MONO[j]] ?? 0))) continue;
          matrix[i][j] += n.deck_count;
          if (i !== j) matrix[j][i] += n.deck_count;
        }
      }
    }

    // 3. Per-color stats (using color_mask so multi-color cards count toward each color)
    const totalDecks = nodes[0]?.total_decks || 1;
    const perColor: ColorStat[] = MONO.map(c => {
      const bit   = COLOR_BITS[c] ?? 0;
      const cards = nodes.filter(n => n.color_mask & bit);
      return {
        color:          c,
        cardCount:      cards.length,
        avgCardsPerDeck: cards.reduce((s, n) => s + n.deck_count * n.avg_qty, 0) / totalDecks,
        topCards:       [...cards].sort((a, b) => b.deck_count - a.deck_count).slice(0, 30),
      };
    });

    // 4. Inclusion distribution per color_cat — only "played" cards (≥0.5% inclusion)
    const INCL_THRESHOLD = 0.5;
    const inclDist: InclDist[] = ALL_CATS.map(cat => {
      const vals = nodes
        .filter(n => n.color_cat === cat && n.inclusion_pct >= INCL_THRESHOLD)
        .map(n => n.inclusion_pct)
        .sort((a, b) => a - b);
      if (vals.length < 5) return null;
      const q = (p: number) => vals[Math.round(p * (vals.length - 1))];
      return { cat, min: q(0), q1: q(0.25), median: q(0.5), q3: q(0.75), max: q(1), count: vals.length };
    }).filter(Boolean) as InclDist[];

    return { metaShare, matrix, perColor, inclDist };
  }, [nodes]);
}

// ── Root component ────────────────────────────────────────────────────────────

interface Props {
  nodes:         CardNode[];
  archetypeData: ArchetypeData | null;
}

export function ColorsView({ nodes, archetypeData }: Props) {
  const { metaShare, matrix, perColor, inclDist } = useColorStats(nodes);

  const l1Archetypes = useMemo(
    () => (archetypeData?.archetypes ?? [])
      .filter(a => a.level === 1 && a.meta_share != null && a.color_profile)
      .sort((a, b) => (b.meta_share ?? 0) - (a.meta_share ?? 0))
      .slice(0, 16),
    [archetypeData],
  );

  return (
    <div className="colors-view">
      <div className="cv-top-row">
        <MetaShareDonut  data={metaShare} />
        <PairHeatmap     matrix={matrix} />
        <InclusionBoxPlots data={inclDist} />
        {l1Archetypes.length > 0 && <ArchetypeColorBars archetypes={l1Archetypes} />}
      </div>
      <PerColorSection   stats={perColor} />
    </div>
  );
}

// ── Donut ─────────────────────────────────────────────────────────────────────

function MetaShareDonut({ data }: { data: MetaEntry[] }) {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const el = svgRef.current;
    if (!el || !data.length) return;
    const S = 230, cx = S / 2, cy = S / 2, ro = 100, ri = ro * 0.54;

    const svg = d3.select(el).attr('width', S).attr('height', S);
    svg.selectAll('*').remove();

    const pie = d3.pie<MetaEntry>().value(d => d.count).sort(null);
    const arc = d3.arc<d3.PieArcDatum<MetaEntry>>().innerRadius(ri).outerRadius(ro);
    const g   = svg.append('g').attr('transform', `translate(${cx},${cy})`);

    g.selectAll('path').data(pie(data)).join('path')
      .attr('d', arc)
      .attr('fill', d => COLOR_HEX[d.data.cat as ColorCat] ?? '#555')
      .attr('stroke', '#0d1117').attr('stroke-width', 1.5);

    g.append('text').attr('text-anchor', 'middle').attr('dy', '-0.3em')
      .attr('fill', '#8b949e').attr('font-size', 10).text('color');
    g.append('text').attr('text-anchor', 'middle').attr('dy', '1em')
      .attr('fill', '#e6edf3').attr('font-size', 13).attr('font-weight', 600).text('share');
  }, [data]);

  return (
    <div className="cv-card cv-donut-card">
      <div className="cv-section-label">Color Meta Share</div>
      <div className="cv-donut-inner">
        <svg ref={svgRef} style={{ flexShrink: 0 }} />
        <div className="cv-donut-legend">
          {data.map(d => (
            <div key={d.cat} className="cv-legend-item">
              <span className="cv-legend-dot" style={{ background: COLOR_HEX[d.cat as ColorCat] ?? '#555' }} />
              <span className="cv-legend-name">{d.cat === 'Colorless' ? 'Colorless' : (COLOR_LABEL[d.cat as ColorCat] ?? d.cat)}</span>
              <span className="cv-legend-pct">{(d.pct * 100).toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Heatmap ───────────────────────────────────────────────────────────────────

function PairHeatmap({ matrix }: { matrix: number[][] }) {
  const maxVal = Math.max(...matrix.flat(), 1);

  return (
    <div className="cv-card cv-heatmap-card">
      <div className="cv-section-label">Color Pair Co-occurrence</div>
      <p className="cv-heatmap-hint">Each cell = total deck slots shared by both colors.</p>
      <p className="cv-heatmap-hint">Diagonal = mono presence.</p>
      <div className="cv-heatmap">
        {/* Corner + column headers */}
        <div />
        {MONO.map(c => (
          <div key={c} className="cv-heatmap-header" style={{ color: COLOR_HEX[c as ColorCat] }}>{c}</div>
        ))}
        {/* Rows */}
        {MONO.map((rowC, i) => [
          <div key={`lbl-${i}`} className="cv-heatmap-header" style={{ color: COLOR_HEX[rowC as ColorCat] }}>{rowC}</div>,
          ...MONO.map((colC, j) => {
            const val       = matrix[i][j];
            const intensity = val / maxVal;
            const isDiag    = i === j;
            const bg        = isDiag
              ? COLOR_HEX[rowC as ColorCat]
              : `linear-gradient(135deg, ${COLOR_HEX[rowC as ColorCat]}, ${COLOR_HEX[colC as ColorCat]})`;
            const label     = val >= 1000
              ? `${(val / 1000).toFixed(0)}k`
              : val > 0 ? String(val) : '';
            return (
              <div
                key={`${i}-${j}`}
                className="cv-heatmap-cell"
                style={{ background: bg, opacity: val > 0 ? 1 : 0.12 }}
                title={`${rowC}${isDiag ? '' : colC}: ${val.toLocaleString()} deck slots`}
              >
                {label && (!isDiag || intensity > 0.25) && <span className="cv-heatmap-val">{label}</span>}
              </div>
            );
          }),
        ])}
      </div>
    </div>
  );
}

// ── Per-color breakdown ───────────────────────────────────────────────────────

function PerColorSection({ stats }: { stats: ColorStat[] }) {
  return (
    <div className="cv-card">
      <div className="cv-section-label">Per-Color Breakdown</div>
      <div className="cv-per-color-grid">
        {stats.map(s => {
          const hex     = COLOR_HEX[s.color as ColorCat];
          const textClr = s.color === 'W' ? '#1a1a1a' : '#ffffff';
          const maxPct  = Math.max(...s.topCards.map(c => c.inclusion_pct), 0.01);
          return (
            <div key={s.color} className="cv-color-panel" style={{ borderTopColor: hex }}>
              <div className="cv-color-panel__header">
                <span className="cv-color-pip" style={{ background: hex, color: textClr }}>{s.color}</span>
                <span className="cv-color-panel__name">{COLOR_LABEL[s.color as ColorCat]}</span>
              </div>
              <div className="cv-color-panel__stats">
                <MiniStat label="Cards"           value={s.cardCount.toLocaleString()} />
                <MiniStat label="Avg cards / deck" value={s.avgCardsPerDeck.toFixed(1)} />
              </div>
              <div className="cv-color-panel__top">
                {s.topCards.map(card => (
                  <div key={card.name} className="cv-top-row-item">
                    <span className="cv-top-name" title={card.name}>{card.name}</span>
                    <div className="cv-top-bar-wrap">
                      <div className="cv-top-bar" style={{
                        width: `${(card.inclusion_pct / maxPct) * 100}%`,
                        background: hex,
                      }} />
                    </div>
                    <span className="cv-top-pct">{card.inclusion_pct.toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Box plots ─────────────────────────────────────────────────────────────────

function InclusionBoxPlots({ data }: { data: InclDist[] }) {
  const svgRef   = useRef<SVGSVGElement>(null);
  const wrapRef  = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el   = svgRef.current;
    const wrap = wrapRef.current;
    if (!el || !data.length) return;

    const W  = wrap?.clientWidth ?? 600;
    const ML = 72, MR = 20, MT = 10, MB = 28;
    const rowH = 32;
    const H    = MT + rowH * data.length + MB;
    const plotW = W - ML - MR;
    const plotH = H - MT - MB;

    const svg = d3.select(el).attr('width', W).attr('height', H);
    svg.selectAll('*').remove();

    const allMax = Math.ceil(Math.max(...data.map(d => d.max), 1) / 5) * 5;
    const x = d3.scaleLinear().domain([0, allMax]).range([0, plotW]);
    const g  = svg.append('g').attr('transform', `translate(${ML},${MT})`);

    // Gridlines
    g.append('g')
      .call(d3.axisBottom(x).ticks(5).tickSize(-plotH).tickFormat(() => ''))
      .attr('transform', `translate(0,${plotH})`)
      .call(a => a.select('.domain').remove())
      .call(a => a.selectAll('.tick line').attr('stroke', '#21262d').attr('stroke-dasharray', '3,3'));

    // X axis
    g.append('g')
      .attr('transform', `translate(0,${plotH})`)
      .call(d3.axisBottom(x).ticks(5).tickFormat(v => `${v}%`))
      .call(a => a.select('.domain').attr('stroke', '#30363d'))
      .call(a => a.selectAll('.tick line').attr('stroke', '#30363d'))
      .call(a => a.selectAll('text').attr('fill', '#6e7681').attr('font-size', 9));

    // Box plots
    data.forEach((d, i) => {
      const cy    = MT + i * rowH + rowH / 2 - MT;
      const color = COLOR_HEX[d.cat as ColorCat] ?? '#8b949e';
      const bh    = 13;

      // Whisker line
      g.append('line')
        .attr('x1', x(d.min)).attr('y1', cy)
        .attr('x2', x(d.max)).attr('y2', cy)
        .attr('stroke', color).attr('stroke-opacity', 0.35).attr('stroke-width', 1);

      // Min/max ticks
      for (const v of [d.min, d.max]) {
        g.append('line')
          .attr('x1', x(v)).attr('y1', cy - 5)
          .attr('x2', x(v)).attr('y2', cy + 5)
          .attr('stroke', color).attr('stroke-opacity', 0.5).attr('stroke-width', 1);
      }

      // IQR box
      g.append('rect')
        .attr('x', x(d.q1)).attr('y', cy - bh / 2)
        .attr('width', Math.max(1, x(d.q3) - x(d.q1))).attr('height', bh)
        .attr('fill', color).attr('fill-opacity', 0.22)
        .attr('stroke', color).attr('stroke-opacity', 0.55).attr('rx', 2);

      // Median
      g.append('line')
        .attr('x1', x(d.median)).attr('y1', cy - bh / 2)
        .attr('x2', x(d.median)).attr('y2', cy + bh / 2)
        .attr('stroke', color).attr('stroke-width', 2.5);

      // Row label
      g.append('text')
        .attr('x', -8).attr('y', cy).attr('dy', '0.35em')
        .attr('text-anchor', 'end').attr('font-size', 10).attr('fill', color)
        .text(d.cat === 'Colorless' ? 'ø' : d.cat);

      // Count label
      g.append('text')
        .attr('x', x(d.max) + 6).attr('y', cy).attr('dy', '0.35em')
        .attr('font-size', 8).attr('fill', '#6e7681')
        .text(`n=${d.count}`);
    });
  }, [data]);

  return (
    <div className="cv-card cv-boxplot-card">
      <div className="cv-section-label">Inclusion Rate Distribution</div>
      <p className="cv-heatmap-hint">Cards with ≥0.5% inclusion only. Box = IQR (Q1–Q3). Line = median. Whiskers = min/max.</p>
      <div ref={wrapRef} className="cv-boxplot-wrap">
        <svg ref={svgRef} style={{ width: '100%', display: 'block' }} />
      </div>
    </div>
  );
}

// ── Archetype color bars ──────────────────────────────────────────────────────

function archLabel(a: ArchetypeNode): string {
  if (a.name) return a.name;
  const cards = a.top_cards;
  if (cards && cards.length >= 2) return `${cards[0].card} · ${cards[1].card}`;
  if (cards && cards.length === 1) return cards[0].card;
  return `Archetype ${a.id}`;
}

function ArchetypeColorBars({ archetypes }: { archetypes: ArchetypeNode[] }) {
  return (
    <div className="cv-card cv-archbars-card">
      <div className="cv-section-label">Archetype Color Profiles</div>
      <p className="cv-heatmap-hint">Segments = color composition. % = meta share.</p>
      <div className="cv-arch-bars">
        {archetypes.map(arch => {
          const profile = arch.color_profile!;
          return (
            <div key={arch.id} className="cv-arch-row">
              <div className="cv-arch-label" title={archLabel(arch)}>{archLabel(arch)}</div>
              <div className="cv-arch-bar-wrap">
                <div className="cv-arch-bar" style={{ width: '100%' }}>
                  {MONO.map(c => {
                    const pct = profile[c] ?? 0;
                    if (pct < 0.03) return null;
                    return (
                      <div key={c} className="cv-arch-seg"
                        style={{ width: `${pct * 100}%`, background: COLOR_HEX[c as ColorCat] }}
                        title={`${c}: ${(pct * 100).toFixed(0)}%`}
                      />
                    );
                  })}
                </div>
              </div>
              <div className="cv-arch-share">{((arch.meta_share ?? 0) * 100).toFixed(1)}%</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Shared micro-components ───────────────────────────────────────────────────

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="cv-mini-stat">
      <span className="cv-mini-stat__label">{label}</span>
      <span className="cv-mini-stat__value">{value}</span>
    </div>
  );
}
