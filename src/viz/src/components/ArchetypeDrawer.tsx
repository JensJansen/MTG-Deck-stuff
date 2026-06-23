import type { ArchetypeData, ArchetypeNode, ColorCat } from '../types';
import { COLOR_HEX } from '../constants';
import { archetypeLabel } from './ArchetypeView';
import './ArchetypeDrawer.css';

const CMC_LABELS = ['0', '1', '2', '3', '4', '5', '6+'];
const COLOR_NAMES: Record<string, string> = {
  W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green',
};

interface Props {
  open:      boolean;
  archetype: ArchetypeNode | null;
  data:      ArchetypeData | null;
  onClose:   () => void;
}

export function ArchetypeDrawer({ open, archetype, data, onClose }: Props) {
  return (
    <div className={`arch-drawer${open ? ' open' : ''}`}>
      {archetype && data && <DrawerContent archetype={archetype} data={data} onClose={onClose} />}
    </div>
  );
}

function DrawerContent({ archetype, data, onClose }: {
  archetype: ArchetypeNode;
  data:      ArchetypeData;
  onClose:   () => void;
}) {
  const label        = archetypeLabel(archetype);
  const subArchetypes = data.archetypes.filter(a => a.level === 2 && a.parent_id === archetype.id);
  const parent        = archetype.parent_id != null
    ? data.archetypes.find(a => a.id === archetype.parent_id) ?? null
    : null;

  return (
    <>
      <div className="arch-drawer__header">
        <button className="arch-drawer__back" onClick={onClose}>← Back</button>
        <span className="arch-drawer__title" title={label}>{label}</span>
      </div>

      <div className="arch-drawer__summary">
        <SummaryCell label="Decks"      value={archetype.member_count.toLocaleString()} />
        <SummaryCell label="Meta share" value={
          archetype.meta_share != null ? `${(archetype.meta_share * 100).toFixed(1)}%` : '—'
        } />
        {archetype.level === 1 && (
          <SummaryCell label="Sub-archetypes" value={String(subArchetypes.length)} />
        )}
        {archetype.level === 2 && parent && (
          <SummaryCell label="Archetype" value={archetypeLabel(parent)} />
        )}
      </div>

      <div className="arch-drawer__body">

        {/* Color profile */}
        {archetype.color_profile && (
          <section className="arch-drawer__section">
            <div className="arch-drawer__section-label">Color profile</div>
            <ColorBar profile={archetype.color_profile} />
          </section>
        )}

        {/* CMC curve */}
        {archetype.cmc_curve && archetype.cmc_curve.length > 0 && (
          <section className="arch-drawer__section">
            <div className="arch-drawer__section-label">CMC curve</div>
            <CmcChart curve={archetype.cmc_curve} />
          </section>
        )}

        {/* Keystone cards (L2 only) */}
        {archetype.keystone_cards && archetype.keystone_cards.length > 0 && (
          <section className="arch-drawer__section">
            <div className="arch-drawer__section-label">Keystone cards</div>
            <table className="arch-drawer__table">
              <thead>
                <tr>
                  <th className="arch-drawer__th">Card</th>
                  <th className="arch-drawer__th arch-drawer__th--r">In</th>
                  <th className="arch-drawer__th arch-drawer__th--r">Out</th>
                  <th className="arch-drawer__th arch-drawer__th--r">Diff</th>
                </tr>
              </thead>
              <tbody>
                {archetype.keystone_cards.map(k => (
                  <tr key={k.card} className="arch-drawer__row">
                    <td className="arch-drawer__td">{k.card}</td>
                    <td className="arch-drawer__td arch-drawer__td--num">
                      {(k.p_in * 100).toFixed(0)}%
                    </td>
                    <td className="arch-drawer__td arch-drawer__td--num arch-drawer__td--faint">
                      {(k.p_out * 100).toFixed(0)}%
                    </td>
                    <td className="arch-drawer__td arch-drawer__td--num"
                      style={{ color: diffColor(k.diff) }}>
                      +{(k.diff * 100).toFixed(0)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {/* Top cards */}
        {archetype.top_cards && archetype.top_cards.length > 0 && (
          <section className="arch-drawer__section">
            <div className="arch-drawer__section-label">Top cards</div>
            <table className="arch-drawer__table">
              <thead>
                <tr>
                  <th className="arch-drawer__th">Card</th>
                  <th className="arch-drawer__th arch-drawer__th--r">Inclusion</th>
                  {archetype.top_cards.some(c => c.avg_qty != null) && (
                    <th className="arch-drawer__th arch-drawer__th--r">Avg qty</th>
                  )}
                </tr>
              </thead>
              <tbody>
                {archetype.top_cards.map(c => (
                  <tr key={c.card} className="arch-drawer__row">
                    <td className="arch-drawer__td">{c.card}</td>
                    <td className="arch-drawer__td arch-drawer__td--num">
                      {(c.pct * 100).toFixed(1)}%
                    </td>
                    {c.avg_qty != null && (
                      <td className="arch-drawer__td arch-drawer__td--num arch-drawer__td--faint">
                        {c.avg_qty.toFixed(1)}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

      </div>
    </>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="arch-drawer__cell-label">{label}</div>
      <div className="arch-drawer__cell-value">{value}</div>
    </div>
  );
}

function ColorBar({ profile }: { profile: NonNullable<ArchetypeNode['color_profile']> }) {
  const COLORS = ['W', 'U', 'B', 'R', 'G'] as const;
  const total  = COLORS.reduce((s, c) => s + (profile[c] ?? 0), 0);
  if (total === 0) return <div className="arch-drawer__muted">No color data</div>;

  return (
    <div className="color-bar">
      {COLORS.map(c => {
        const pct = ((profile[c] ?? 0) / total) * 100;
        if (pct < 1) return null;
        return (
          <div key={c} className="color-bar__seg"
            style={{ width: `${pct}%`, background: COLOR_HEX[c as ColorCat] }}
            title={`${COLOR_NAMES[c]}: ${pct.toFixed(1)}%`}
          />
        );
      })}
    </div>
  );
}

function CmcChart({ curve }: { curve: number[] }) {
  const max = Math.max(...curve, 0.001);
  return (
    <div className="cmc-chart">
      {curve.map((v, i) => (
        <div key={i} className="cmc-chart__col">
          <div className="cmc-chart__bar" style={{ height: `${Math.round((v / max) * 52)}px` }} />
          <div className="cmc-chart__label">{CMC_LABELS[i] ?? i}</div>
        </div>
      ))}
    </div>
  );
}

function diffColor(diff: number): string {
  if (diff >= 0.5) return 'var(--accent-green)';
  if (diff >= 0.3) return 'var(--accent-amber)';
  return 'var(--text-muted)';
}
