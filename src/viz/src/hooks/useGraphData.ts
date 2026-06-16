import { useState, useEffect } from 'react';
import type { GraphData, Manifest } from '../types';

export function useManifest() {
  const [formats, setFormats] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/data/manifest.json')
      .then(r => r.json() as Promise<Manifest>)
      .then(m => { setFormats(m.formats); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  return { formats, loading };
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
