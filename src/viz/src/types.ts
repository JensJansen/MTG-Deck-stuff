export type ColorCat = 'W' | 'U' | 'B' | 'R' | 'G' | 'Multi' | 'Colorless';

export interface CardNode {
  name: string;
  x: number;
  y: number;
  color_cat: ColorCat;
  color_mask: number;      // bitmask: W=1, U=2, B=4, R=8, G=16; 0 = colorless
  deck_count: number;
  total_decks: number;
  inclusion_pct: number;
  avg_qty: number;
  image_uri: string | null;
}

/** Compact ego-network partner entry. */
export interface EgoPartner {
  n: string;   // card name
  c: number;   // co-occurrence count
  l: number;   // lift
  j: number;   // jaccard
}

export interface GraphData {
  format: string;
  nodes: CardNode[];
  ego: Record<string, EgoPartner[]>;
  edges: [number, number, number][];  // [a_idx, b_idx, dist_int]
}

export interface Manifest {
  formats: string[];
}

/** Compact focus partner tuple: [cardName, coOccurrenceCount, lift] */
export type FocusPartner = [string, number, number];

/** Per-card focus data: maps each card name to all partners with ≥5 co-occurrences, sorted desc by count */
export type FocusData = Record<string, FocusPartner[]>;

/** A node in the focus subgraph */
export interface FocusGraphNode {
  id: number;
  depth: 0 | 1 | 2;
}

/** An edge to display in the focus graph */
export interface FocusGraphEdge {
  source: number;
  target: number;
  jaccard: number;      // edge weight for opacity encoding
  idealLength: number;  // target pixel length for fcose layout
}

/** Complete data bundle passed to FocusGraph */
export interface FocusGraphData {
  focusNodeId: number;
  nodes: FocusGraphNode[];
  edges: FocusGraphEdge[];
}
