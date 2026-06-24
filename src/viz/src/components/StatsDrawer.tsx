import { useState, useMemo, useEffect } from 'react';
import type { CardNode, EgoPartner } from '../types';
import './StatsDrawer.css';

type SortCol = 'n' | 'c' | 'l' | 'j';

interface Props {
  open: boolean;
  node: CardNode | null;
  partners: EgoPartner[];
  onClose: () => void;
  onNodeClick: (name: string) => void;
}

function liftColor(lift: number): string {
  if (lift >= 2)   return 'var(--accent-green)';
  if (lift >= 1.2) return 'var(--accent-amber)';
  return 'var(--text-muted)';
}

export function StatsDrawer({ open, node, partners, onClose, onNodeClick }: Props) {
  const [sortCol, setSortCol] = useState<SortCol>('c');
  const [sortAsc, setSortAsc] = useState(false);
  useEffect(() => { setSortCol('c'); setSortAsc(false); }, [node?.name]);

  const sorted = useMemo(() => [...partners].sort((a, b) => {
    const av = a[sortCol], bv = b[sortCol];
    if (typeof av === 'string') return sortAsc ? av.localeCompare(bv as string) : (bv as string).localeCompare(av);
    return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number);
  }), [partners, sortCol, sortAsc]);

  function handleColClick(col: SortCol) {
    if (sortCol === col) setSortAsc(v => !v);
    else { setSortCol(col); setSortAsc(col === 'n'); }
  }

  const arrow = (col: SortCol) => sortCol === col ? (sortAsc ? ' ↑' : ' ↓') : '';

  return (
    <div className={`stats-drawer drawer-slide${open ? ' open' : ''}`}>
      <div className="stats-drawer__header">
        <button className="stats-drawer__back-btn" onClick={onClose}>← Back</button>
        <span className="stats-drawer__title">{node?.name}</span>
      </div>

      {node && (
        <div className="stats-drawer__summary">
          <SummaryCell label="Inclusion"  value={`${node.inclusion_pct}%`} />
          <SummaryCell label="Decks"      value={node.deck_count.toLocaleString()} />
          <SummaryCell label="Avg copies" value={String(node.avg_qty)} />
          <SummaryCell label="Partners"   value={String(partners.length)} />
        </div>
      )}

      <div className="stats-drawer__body">
        {partners.length === 0 ? (
          <div className="stats-drawer__empty">No co-occurrence data for this card.</div>
        ) : (
          <table className="stats-drawer__table">
            <thead className="stats-drawer__thead">
              <tr>
                <th className="stats-drawer__th"           style={{ color: sortCol === 'n' ? 'var(--accent-blue)' : 'var(--text-faint)' }} onClick={() => handleColClick('n')}>Card{arrow('n')}</th>
                <th className="stats-drawer__th stats-drawer__th--right" style={{ color: sortCol === 'c' ? 'var(--accent-blue)' : 'var(--text-faint)' }} onClick={() => handleColClick('c')}>Co-occur{arrow('c')}</th>
                <th className="stats-drawer__th stats-drawer__th--right" style={{ color: sortCol === 'l' ? 'var(--accent-blue)' : 'var(--text-faint)' }} onClick={() => handleColClick('l')}>Lift{arrow('l')}</th>
                <th className="stats-drawer__th stats-drawer__th--right" style={{ color: sortCol === 'j' ? 'var(--accent-blue)' : 'var(--text-faint)' }} onClick={() => handleColClick('j')}>Jaccard{arrow('j')}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(p => (
                <tr key={p.n} className="stats-drawer__row">
                  <td className="stats-drawer__td stats-drawer__td--name" onClick={() => onNodeClick(p.n)} title={p.n}>{p.n}</td>
                  <td className="stats-drawer__td stats-drawer__td--num">{p.c.toLocaleString()}</td>
                  <td className="stats-drawer__td stats-drawer__td--lift" style={{ color: liftColor(p.l) }}>{p.l.toFixed(2)}</td>
                  <td className="stats-drawer__td stats-drawer__td--num">{p.j.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="stats-drawer__summary-label">{label}</div>
      <div className="stats-drawer__summary-value">{value}</div>
    </div>
  );
}
