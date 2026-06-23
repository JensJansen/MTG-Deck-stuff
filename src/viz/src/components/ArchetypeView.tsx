import { useEffect, useRef, useState, useMemo } from 'react';
import { hierarchy, treemap, treemapSquarify } from 'd3';
import type { HierarchyRectangularNode } from 'd3';
import { COLOR_HEX } from '../constants';
import type { ArchetypeData, ArchetypeNode, ColorCat } from '../types';
import './ArchetypeView.css';

// ── Helpers ───────────────────────────────────────────────────────────────────

interface TreeDatum {
  archetype: ArchetypeNode | null;  // null = noise remainder tile
  value?:    number;
  children?: TreeDatum[];
}

type HNode = HierarchyRectangularNode<TreeDatum>;

function dominantColor(profile: ArchetypeNode['color_profile']): ColorCat {
  if (!profile) return 'Colorless';
  const COLORS = ['W', 'U', 'B', 'R', 'G'] as const;
  const total = COLORS.reduce((s, c) => s + (profile[c] ?? 0), 0);
  if (total < 0.05) return 'Colorless';
  const maxPct = Math.max(...COLORS.map(c => profile[c] ?? 0));
  const tops   = COLORS.filter(c => (profile[c] ?? 0) >= maxPct * 0.55);
  if (tops.length !== 1) return 'Multi';
  return tops[0] as ColorCat;
}

export function archetypeLabel(node: ArchetypeNode): string {
  if (node.name) return node.name;
  // For L2: prefer keystone cards — they're what differentiates this sub-archetype
  // from its siblings.  Top cards would be identical across siblings.
  if (node.level === 2 && node.keystone_cards && node.keystone_cards.length > 0) {
    const sorted = [...node.keystone_cards].sort((a, b) => b.diff - a.diff);
    const top2   = sorted.slice(0, 2).map(k => k.card);
    return top2.join(' · ');
  }
  const cards = node.top_cards;
  if (cards && cards.length >= 2) return `${cards[0].card} · ${cards[1].card}`;
  if (cards && cards.length === 1) return cards[0].card;
  return `Archetype ${node.id}`;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  data:       ArchetypeData;
  onSelect:   (node: ArchetypeNode) => void;
  selectedId: number | null;
}

export function ArchetypeView({ data, onSelect, selectedId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);

  // Measure container and re-compute on resize
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver(entries => {
      const r = entries[0].contentRect;
      setDims({ w: Math.floor(r.width), h: Math.floor(r.height) });
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // Build d3 treemap layout
  const tiles = useMemo<HNode[]>(() => {
    if (!dims || dims.w < 10 || dims.h < 10) return [];

    const l1s = data.archetypes.filter(a => a.level === 1);
    const l2ByParent = new Map<number, ArchetypeNode[]>();
    for (const a of data.archetypes) {
      if (a.level === 2 && a.parent_id != null) {
        const arr = l2ByParent.get(a.parent_id) ?? [];
        arr.push(a);
        l2ByParent.set(a.parent_id, arr);
      }
    }

    const rootDatum: TreeDatum = {
      archetype: null,
      children: l1s.map(l1 => {
        const l2s = l2ByParent.get(l1.id) ?? [];
        if (l2s.length === 0) return { archetype: l1, value: l1.member_count };
        const l2Sum     = l2s.reduce((s, c) => s + c.member_count, 0);
        const remainder = Math.max(0, l1.member_count - l2Sum);
        const children: TreeDatum[] = l2s.map(l2 => ({ archetype: l2, value: l2.member_count }));
        if (remainder > 0) children.push({ archetype: null, value: remainder });
        return { archetype: l1, children };
      }),
    };

    const root = hierarchy<TreeDatum>(rootDatum)
      .sum(d => d.value ?? 0)
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));

    const layoutRoot = treemap<TreeDatum>()
      .size([dims.w, dims.h])
      .paddingOuter(4)
      .paddingTop(22)
      .paddingInner(2)
      .tile(treemapSquarify)(root);

    return layoutRoot.descendants().filter(n => n.depth > 0);
  }, [data, dims]);

  const containers = tiles.filter(n => n.depth === 1 && n.children);
  const leaves     = tiles.filter(n => !n.children);

  return (
    <div ref={containerRef} className="archetype-view">
      {dims && (
        <>
          {/* L1 containers — background frames; pointer-events disabled on the body
               so clicks fall through to L2 tiles. Only the header strip is clickable. */}
          {containers.map(n => {
            const arch  = n.data.archetype;
            const label = arch ? archetypeLabel(arch) : '';
            const color = COLOR_HEX[dominantColor(arch?.color_profile ?? null)];
            return (
              <div
                key={`c-${arch?.id ?? 'root'}`}
                className="at-container"
                style={{
                  left: n.x0, top: n.y0,
                  width: n.x1 - n.x0, height: n.y1 - n.y0,
                  borderColor: `${color}55`,
                  pointerEvents: 'none',
                }}
              >
                <div
                  className="at-container__header"
                  style={{ color, pointerEvents: 'auto', cursor: 'pointer' }}
                  onClick={() => arch && onSelect(arch)}
                >
                  <span className="at-container__name">{label}</span>
                  {arch?.meta_share != null && (
                    <span className="at-container__share">
                      {(arch.meta_share * 100).toFixed(1)}%
                    </span>
                  )}
                </div>
              </div>
            );
          })}

          {/* Leaf tiles — L2 sub-archetypes + leaf L1s + noise remainders */}
          {leaves.map((n, i) => {
            const arch = n.data.archetype;

            // Noise remainder (deck not fitting any sub-archetype)
            if (!arch) {
              return (
                <div key={`noise-${i}`} className="at-tile at-tile--noise"
                  style={{ left: n.x0, top: n.y0, width: n.x1 - n.x0, height: n.y1 - n.y0 }} />
              );
            }

            const color      = COLOR_HEX[dominantColor(arch.color_profile)];
            const w          = n.x1 - n.x0;
            const h          = n.y1 - n.y0;
            const isSelected = arch.id === selectedId;
            const isLeafL1   = arch.level === 1;
            const showLabel  = w > 56 && h > 28;
            const showShare  = w > 90 && h > 46 && isLeafL1 && arch.meta_share != null;

            return (
              <div
                key={`t-${arch.id}`}
                className={[
                  'at-tile',
                  isSelected ? 'at-tile--selected' : '',
                  isLeafL1   ? 'at-tile--l1'       : '',
                ].join(' ').trim()}
                style={{
                  left: n.x0, top: n.y0, width: w, height: h,
                  // L1 leaf tiles get more opacity — they're standalone archetypes
                  // L2 tiles are nested variants so slightly more subtle
                  background:  isLeafL1 ? `${color}40` : `${color}28`,
                  borderColor: isSelected ? color : (isLeafL1 ? `${color}66` : `${color}44`),
                  boxShadow:   isSelected ? `inset 0 0 0 2px ${color}` : undefined,
                }}
                onClick={() => onSelect(arch)}
              >
                {showLabel && (
                  <div className="at-tile__label"
                    style={{ color: isSelected ? color : 'var(--text-muted)' }}>
                    {archetypeLabel(arch)}
                  </div>
                )}
                {showShare && (
                  <div className="at-tile__share" style={{ color: `${color}99` }}>
                    {(arch.meta_share! * 100).toFixed(1)}%
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
