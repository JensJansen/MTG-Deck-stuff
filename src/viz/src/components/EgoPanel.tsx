import { useState, useEffect } from 'react';
import { COLOR_LABEL } from '../constants';
import { EgoNetwork } from './EgoNetwork';
import type { CardNode, EgoPartner } from '../types';
import './EgoPanel.css';

interface Props {
  node: CardNode | null;
  partners: EgoPartner[];
  nameIndex: Record<string, number>;
  nodes: CardNode[];
  onClose: () => void;
  onRefocus: () => void;
  onViewStats: () => void;
  onNodeClick: (name: string) => void;
}

export function EgoPanel({ node, partners, nameIndex, nodes, onClose, onRefocus, onViewStats, onNodeClick }: Props) {
  const [showFullCard, setShowFullCard] = useState(false);
  useEffect(() => { setShowFullCard(false); }, [node]);

  if (!node) return null;

  return (
    <div className="ego-panel panel-slide open">
      {/* Card art section */}
      {node.image_uri && (
        <div className="ego-panel__art-section">
          {/* Art crop + expand button — collapses out as full card expands in */}
          <div className="ego-panel__art-crop-wrap" style={{ maxHeight: showFullCard ? 0 : 500 }}>
            <div className="ego-panel__art">
              <img src={node.image_uri.replace('/normal/', '/art_crop/')} alt={node.name} />
            </div>
            <button className="ego-panel__toggle-btn" onClick={() => setShowFullCard(true)}>
              <span className="ego-panel__toggle-chevron">▼</span>
              Full card
            </button>
          </div>

          {/* Full card — expands in as crop collapses out */}
          <div className="ego-panel__full-card" style={{ maxHeight: showFullCard ? 640 : 0 }}>
            <img src={node.image_uri} alt={node.name} />
            <button className="ego-panel__toggle-btn ego-panel__toggle-btn--bottom" onClick={() => setShowFullCard(false)}>
              <span className="ego-panel__toggle-chevron" style={{ transform: 'rotate(180deg)' }}>▼</span>
              Collapse
            </button>
          </div>
        </div>
      )}

      {/* Scrollable body — everything below the art */}
      <div className="ego-panel__scroll-body">

        {/* Header */}
        <div className="ego-panel__header">
          <div>
            <div className="ego-panel__card-name">{node.name}</div>
            <div className="ego-panel__color-label">{COLOR_LABEL[node.color_cat] ?? node.color_cat}</div>
          </div>
          <button className="ego-panel__close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>

        {/* Stats grid */}
        <div className="ego-panel__stats">
          <Stat label="Inclusion rate" value={`${node.inclusion_pct}%`} />
          <Stat label="Decks"          value={node.deck_count.toLocaleString()} />
          <Stat label="Avg copies"     value={String(node.avg_qty)} />
          <Stat label="Total decks"    value={node.total_decks.toLocaleString()} />
        </div>

        {/* Action buttons */}
        <button className="ego-panel__btn ego-panel__btn--blue" onClick={onViewStats}>View co-occurrence stats</button>
        <button className="ego-panel__btn ego-panel__btn--muted" onClick={onRefocus}>Refocus on this card</button>

        {/* Ego network */}
        <div className="ego-panel__section-label">Top co-occurrences</div>
        <EgoNetwork center={node} partners={partners} nameIndex={nameIndex} nodes={nodes} onNodeClick={onNodeClick} />

      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="stat-cell__label">{label}</div>
      <div className="stat-cell__value">{value}</div>
    </div>
  );
}
