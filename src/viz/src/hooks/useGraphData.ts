import { useState, useEffect } from 'react';
import type { GraphData, Manifest, ArchetypeData } from '../types';

export function useManifest() {
  const [formats, setFormats]                   = useState<string[]>([]);
  const [archetypeFormats, setArchetypeFormats] = useState<string[]>([]);
  const [loading, setLoading]                   = useState(true);
  const [error, setError]                       = useState<string | null>(null);

  useEffect(() => {
    fetch('/data/manifest.json')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<Manifest>;
      })
      .then(m => {
        setFormats(m.formats ?? []);
        setArchetypeFormats(m.archetype_formats ?? []);
        setLoading(false);
      })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, []);

  return { formats, archetypeFormats, loading, error };
}

export function useGraphData(format: string | null) {
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!format) return;
    setLoading(true);
    setData(null);
    setError(null);
    fetch(`/data/${format}.json`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<GraphData>;
      })
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, [format]);

  return { data, loading, error };
}

export function useArchetypeData(format: string | null) {
  const [data, setData] = useState<ArchetypeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!format) { setData(null); return; }
    setLoading(true);
    setData(null);
    setError(null);
    fetch(`/data/${format}.archetypes.json`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<ArchetypeData>;
      })
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, [format]);

  return { data, loading, error };
}
