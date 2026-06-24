import { useState, useMemo, useRef, useEffect } from 'react';
import { COLOR_HEX, COLOR_LABEL, COLOR_ORDER } from './constants';
import { useManifest, useGraphData, useArchetypeData } from './hooks/useGraphData';
import { useGraphFocus } from './hooks/useGraphFocus';
import { FormatSelector } from './components/FormatSelector';
import { ForceGraph } from './components/ForceGraph';
import { FocusGraph } from './components/FocusGraph';
import { EgoPanel } from './components/EgoPanel';
import { StatsDrawer } from './components/StatsDrawer';
import { ArchetypeView } from './components/ArchetypeView';
import { ArchetypeDrawer } from './components/ArchetypeDrawer';
import { ColorsView } from './components/ColorsView';
import type { ColorCat, ArchetypeNode } from './types';
import './App.css';

export function App() {
  const { formats, archetypeFormats, loading: manifestLoading } = useManifest();

  const [selectedFormat, setSelectedFormat]   = useState<string | null>(null);
  const [viewMode, setViewMode]               = useState<'cards' | 'colors' | 'decks'>('cards');

  // ── Cards mode state ──────────────────────────────────────────────────────
  const [selectedNodeIdx, setSelectedNodeIdx] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen]           = useState(false);
  const [searchQuery, setSearchQuery]         = useState('');
  const [selectedColors, setSelectedColors]   = useState<Set<string>>(new Set());
  const [colorMode, setColorMode]             = useState<'including' | 'exactly'>('including');

  // ── Decks mode state ──────────────────────────────────────────────────────
  const [selectedArchetypeId, setSelectedArchetypeId] = useState<number | null>(null);
  const [archetypeDrawerOpen, setArchetypeDrawerOpen] = useState(false);

  // ── Data loading ──────────────────────────────────────────────────────────
  const { data, loading: dataLoading, error } = useGraphData(selectedFormat);

  const hasArchetypes = archetypeFormats.includes(selectedFormat ?? '');
  const { data: archetypeData, loading: archetypeLoading, error: archetypeError } = useArchetypeData(
    hasArchetypes ? selectedFormat : null
  );

  const nameIndex = useMemo<Record<string, number>>(() => {
    if (!data) return {};
    const idx: Record<string, number> = {};
    data.nodes.forEach((n, i) => { idx[n.name] = i; });
    return idx;
  }, [data]);

  const { focusedNode, isFocused, focusGraphData, applyFocus, resetFocus } =
    useGraphFocus(data?.nodes ?? [], data?.edges ?? [], selectedFormat, data?.ego ?? {}, selectedColors, colorMode, nameIndex);

  const [focusPill, setFocusPill] = useState<string | null>(null);
  const pillTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (pillTimer.current) clearTimeout(pillTimer.current); }, []);
  function showPill(text: string) {
    if (pillTimer.current) clearTimeout(pillTimer.current);
    setFocusPill(text);
    pillTimer.current = setTimeout(() => setFocusPill(null), 5000);
  }

  const highlightedIndices = useMemo<number[] | null>(() => {
    if (!searchQuery || !data) return null;
    const q = searchQuery.toLowerCase();
    return data.nodes.reduce<number[]>((acc, n, i) => {
      if (n.name.toLowerCase().includes(q)) acc.push(i);
      return acc;
    }, []);
  }, [searchQuery, data]);

  const selectedNode = selectedNodeIdx !== null && data ? data.nodes[selectedNodeIdx] : null;
  const partners     = selectedNode ? (data?.ego[selectedNode.name] ?? []) : [];

  const selectedArchetype = useMemo(
    () => archetypeData?.archetypes.find(a => a.id === selectedArchetypeId) ?? null,
    [archetypeData, selectedArchetypeId],
  );

  // ── Handlers ─────────────────────────────────────────────────────────────
  function toggleColor(cat: string) {
    setSelectedColors(prev => { const next = new Set(prev); if (next.has(cat)) next.delete(cat); else next.add(cat); return next; });
  }

  function handleNodeClick(idx: number) {
    if (idx === selectedNodeIdx) { setSelectedNodeIdx(null); setDrawerOpen(false); }
    else                         { setSelectedNodeIdx(idx);  setDrawerOpen(false); }
  }
  function handlePanelClose()             { setSelectedNodeIdx(null); setDrawerOpen(false); }
  function handlePartnerClick(name: string) {
    const idx = nameIndex[name];
    if (idx !== undefined) { setSelectedNodeIdx(idx); setDrawerOpen(false); }
  }
  function handleArchetypeClick(node: ArchetypeNode) {
    setSelectedArchetypeId(node.id);
    setArchetypeDrawerOpen(true);
  }
  function handleFormatSelect(fmt: string) {
    setSelectedFormat(fmt);
    setViewMode('cards');
    setSelectedNodeIdx(null);
    setDrawerOpen(false);
    setSearchQuery('');
    setSelectedColors(new Set());
    setColorMode('including');
    setSelectedArchetypeId(null);
    setArchetypeDrawerOpen(false);
    resetFocus();
  }

  // ── Format selector screen ────────────────────────────────────────────────
  if (!selectedFormat) {
    return (
      <div style={{ width: '100%', height: '100%' }}>
        {manifestLoading ? <LoadingScreen label="Loading..." /> : <FormatSelector formats={formats} onSelect={handleFormatSelect} />}
      </div>
    );
  }

  // ── Loading / error (card data) ───────────────────────────────────────────
  if (dataLoading) return <LoadingScreen label={`Loading ${selectedFormat}...`} />;
  if (error || !data) {
    return (
      <LoadingScreen label={error ?? 'Failed to load data'}>
        <button className="loading-back-btn" onClick={() => setSelectedFormat(null)}>Back</button>
      </LoadingScreen>
    );
  }

  const presentColors = new Set(data.nodes.map(n => n.color_cat));
  const n_l1 = archetypeData?.archetypes.filter(a => a.level === 1).length ?? 0;

  const bottomLabel = viewMode === 'decks'
    ? `${archetypeData ? `${n_l1} archetypes` : 'Decks'} · ${selectedFormat}`
    : `${data.nodes.length.toLocaleString()} cards · ${selectedFormat}`;

  // ── Unified layout ────────────────────────────────────────────────────────
  return (
    <div className="app-root">
      <div className="app-main">

        {/* Top bar — dedicated row, never overlaps content */}
        <div className="app-topbar">
          <ViewToggle mode={viewMode} onSwitch={mode => {
            setViewMode(mode);
            setDrawerOpen(false);
            setArchetypeDrawerOpen(false);
            setSelectedNodeIdx(null);
          }} hasArchetypes={hasArchetypes} />
        </div>

        {/* View content — fills remaining space */}
        <div className="app-content">

          {/* Colors view */}
          {viewMode === 'colors' && (
            <ColorsView
              nodes={data.nodes}
              archetypeData={hasArchetypes ? (archetypeData ?? null) : null}
            />
          )}

          {/* Decks view */}
          {viewMode === 'decks' && (
            archetypeLoading ? (
              <LoadingScreen label="Loading archetypes..." />
            ) : archetypeData ? (
              <ArchetypeView
                data={archetypeData}
                onSelect={handleArchetypeClick}
                selectedId={selectedArchetypeId}
              />
            ) : (
              <LoadingScreen label={archetypeError ?? 'No archetype data — run precompute_archetypes.py first.'} />
            )
          )}

          {/* Cards view — graph */}
          {viewMode === 'cards' && (
            isFocused && focusGraphData ? (
              <FocusGraph key={focusGraphData.focusNodeId} data={focusGraphData} allNodes={data.nodes} onNodeClick={handleNodeClick} />
            ) : (
              <ForceGraph nodes={data.nodes} edges={data.edges} highlightedIndices={highlightedIndices}
                selectedNodeIdx={selectedNodeIdx}
                onNodeClick={handleNodeClick} selectedColors={selectedColors} colorMode={colorMode} />
            )
          )}

          {/* Cards-only overlays */}
          {viewMode === 'cards' && !isFocused && (
            <div className="search-bar">
              <input className="search-input" value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
                placeholder="Search card..." autoComplete="off" spellCheck={false} />
              {searchQuery && <span className="search-clear" onClick={() => setSearchQuery('')}>×</span>}
            </div>
          )}

          {viewMode === 'cards' && (
            <div className="color-filter">
              <div className="color-filter__header">
                <span>Color filter</span>
                {selectedColors.size > 0 && (
                  <button className="color-filter__clear" onClick={() => setSelectedColors(new Set())} title="Clear color filter">×</button>
                )}
              </div>
              <div className="color-filter__pips">
                {COLOR_ORDER.filter(cat => cat !== 'Multi').map(cat => {
                  const active    = selectedColors.has(cat);
                  const label     = cat === 'Colorless' ? 'ø' : cat;
                  const textColor = active ? (cat === 'W' || cat === 'Colorless' ? '#1a1a1a' : '#ffffff') : COLOR_HEX[cat as ColorCat];
                  return (
                    <button key={cat} className="color-filter__pip" onClick={() => toggleColor(cat)} title={COLOR_LABEL[cat as ColorCat]}
                      style={{
                        background: active ? COLOR_HEX[cat as ColorCat] : 'transparent',
                        border:     `2px solid ${COLOR_HEX[cat as ColorCat]}`,
                        boxShadow:  active ? '0 0 0 2px #ffffff' : 'none',
                        opacity:    active ? 1 : 0.55,
                        color:      textColor,
                      }}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
              {selectedColors.size > 0 && (
                <div className="color-filter__modes">
                  {(['including', 'exactly'] as const).map(mode => (
                    <label key={mode} className="color-filter__mode-label"
                      style={{ color: colorMode === mode ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                      <input type="radio" name="colorMode" checked={colorMode === mode} onChange={() => setColorMode(mode)}
                        style={{ margin: 0, cursor: 'pointer', accentColor: 'var(--accent-blue)' }} />
                      {mode.charAt(0).toUpperCase() + mode.slice(1)}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

          {viewMode === 'cards' && isFocused && focusedNode && (
            <div className="focus-badge">
              <span>Focused:</span>
              <span className="focus-badge__name">{focusedNode.name}</span>
              <button className="focus-badge__reset" onClick={resetFocus}>Reset view</button>
            </div>
          )}

          {viewMode === 'cards' && (
            <div className="legend" style={{ right: drawerOpen ? 'calc(var(--drawer-w) - var(--panel-w) + 14px)' : 14 }}>
              {COLOR_ORDER.filter(cat => presentColors.has(cat)).map(cat => (
                <div key={cat} className="legend__item">
                  <span className="legend__dot" style={{ background: COLOR_HEX[cat as ColorCat] }} />
                  {COLOR_LABEL[cat as ColorCat]}
                </div>
              ))}
            </div>
          )}

          {/* Bottom bar */}
          <div className="bottom-bar">
            <span>{bottomLabel}</span>
            <button className="bottom-bar__back" onClick={() => setSelectedFormat(null)}>← formats</button>
          </div>

          {viewMode === 'cards' && (
            <div className="bottom-hint">Scroll: zoom &nbsp;·&nbsp; Drag: pan &nbsp;·&nbsp; Click: inspect</div>
          )}
        </div>
      </div>

      {/* Right panel — only mounted when a node is selected */}
      {selectedNode && (
        <EgoPanel node={selectedNode} partners={partners} nameIndex={nameIndex} nodes={data.nodes}
          onClose={handlePanelClose}
          onRefocus={() => {
            if (selectedNodeIdx !== null) {
              showPill('Loading focus view…');
              applyFocus(selectedNodeIdx).catch((e: unknown) => {
                console.error('applyFocus failed:', e);
                showPill('Failed to load focus data');
              });
            }
          }}
          onViewStats={() => setDrawerOpen(true)}
          onNodeClick={handlePartnerClick}
        />
      )}

      {focusPill && <div className="focus-toast">{focusPill}</div>}

      <StatsDrawer open={drawerOpen} node={selectedNode} partners={partners}
        onClose={() => setDrawerOpen(false)} onNodeClick={handlePartnerClick} />

      <ArchetypeDrawer
        open={archetypeDrawerOpen}
        archetype={selectedArchetype}
        data={archetypeData ?? null}
        onClose={() => setArchetypeDrawerOpen(false)}
      />
    </div>
  );
}

// ── Shared sub-components ─────────────────────────────────────────────────────

function ViewToggle({ mode, onSwitch, hasArchetypes }: {
  mode:          'cards' | 'colors' | 'decks';
  onSwitch:      (m: 'cards' | 'colors' | 'decks') => void;
  hasArchetypes: boolean;
}) {
  return (
    <div className="view-toggle">
      <button className={`view-toggle__btn${mode === 'cards'  ? ' active' : ''}`} onClick={() => onSwitch('cards')}>Cards</button>
      <button className={`view-toggle__btn${mode === 'colors' ? ' active' : ''}`} onClick={() => onSwitch('colors')}>Colors</button>
      {hasArchetypes && (
        <button className={`view-toggle__btn${mode === 'decks' ? ' active' : ''}`} onClick={() => onSwitch('decks')}>Decks</button>
      )}
    </div>
  );
}

function LoadingScreen({ label, children }: { label: string; children?: React.ReactNode }) {
  return (
    <div className="loading-screen">
      <span>{label}</span>
      {children}
    </div>
  );
}
