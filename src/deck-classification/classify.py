"""
Classify a single deck (already in the DB) against the stored archetypes.

Two-stage classification mirrors the pipeline:
  1. Level 1: find the nearest archetype centroid by cosine distance.
  2. Level 2: within that cluster, check keystone card rules to assign a
              sub-archetype (if one exists for this cluster).

This is intended for classifying new decks after the pipeline has been run,
without re-running the full clustering.

Usage:
    from classify import DeckClassifier

    clf = DeckClassifier(fmt="pauper")
    result = clf.classify_deck_id("abc123")
    # {"l1_archetype_id": 5, "l1_confidence": 0.87,
    #  "l2_archetype_id": 12, "l2_confidence": 0.91, "matched_keystones": [...]}

    result = clf.classify_cards(["Lightning Bolt", "Goblin Guide", ...])
"""
import sys
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).parent))
from config import DATABASE_URL


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _centroid_from_bytes(blob: bytes | None) -> np.ndarray | None:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32)


class DeckClassifier:
    def __init__(self, fmt: str, db_url: str = DATABASE_URL) -> None:
        self.fmt    = fmt
        self.db_url = db_url
        self._l1_archetypes: list[dict] = []
        self._l2_archetypes: list[dict] = []
        self._card_embeddings: np.ndarray | None = None
        self._name_to_idx: dict[str, int] = {}
        self._load_archetypes()

    def _load_archetypes(self) -> None:
        conn = psycopg2.connect(self.db_url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, level, parent_id, centroid, keystone_cards, member_count
                    FROM archetypes
                    WHERE format = %s
                    ORDER BY level, id
                """, (self.fmt,))
                for row in cur.fetchall():
                    record = {
                        "id":           row[0],
                        "level":        row[1],
                        "parent_id":    row[2],
                        "centroid":     _centroid_from_bytes(row[3]),
                        "keystones":    row[4] or [],
                        "member_count": row[5],
                    }
                    if row[1] == 1:
                        self._l1_archetypes.append(record)
                    else:
                        self._l2_archetypes.append(record)
        finally:
            conn.close()

        print(f"Loaded {len(self._l1_archetypes)} L1 and {len(self._l2_archetypes)} L2 archetypes for {self.fmt!r}")

    def _deck_embedding(self, card_names: list[str]) -> np.ndarray | None:
        """Mean card embedding for a list of card names. Lazy-loads the embedding matrix."""
        if self._card_embeddings is None:
            self._load_embeddings()
        if self._card_embeddings is None:
            return None
        indices = [self._name_to_idx[n] for n in card_names if n in self._name_to_idx]
        if not indices:
            return None
        return self._card_embeddings[indices].mean(axis=0)

    def _load_embeddings(self) -> None:
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parents[1] / "deck-builder"))
            import vocabulary as voc
            import torch
            from model import DeckTransformer
            from config import DECK_BUILDER_DIR, MODEL_CHECKPOINT, DATA_DIR

            ckpt_path = MODEL_CHECKPOINT
            if not ckpt_path:
                return

            builder_data = str(Path(DECK_BUILDER_DIR) / "data")
            vocab, _, _  = voc.load(builder_data)
            self._name_to_idx = {name: i for i, name in enumerate(vocab)}

            device = "cpu"
            ckpt   = torch.load(ckpt_path, map_location=device)
            model  = DeckTransformer(len(vocab))
            model.load_state_dict(ckpt["model"])
            model.eval()
            self._card_embeddings = model.card_embedding.weight.detach().numpy()
        except Exception as e:
            print(f"  Warning: could not load embeddings ({e}). Falling back to keystone-only classification.")

    def _match_keystones(self, card_set: set[str], keystones: list[dict]) -> list[str]:
        """Return keystone card names present in the deck."""
        return [k["card"] for k in keystones if k["card"] in card_set]

    def classify_cards(self, card_names: list[str]) -> dict:
        """
        Classify a deck given its list of non-land mainboard card names.
        """
        card_set  = set(card_names)
        embedding = self._deck_embedding(card_names)

        # ── Level 1 ────────────────────────────────────────────────────────────
        l1_result = self._classify_l1(embedding, card_set)

        if l1_result is None:
            return {"l1_archetype_id": None, "l1_confidence": 0.0,
                    "l2_archetype_id": None, "l2_confidence": 0.0,
                    "matched_keystones": []}

        # ── Level 2 ────────────────────────────────────────────────────────────
        l2_result = self._classify_l2(l1_result["id"], embedding, card_set)

        return {
            "l1_archetype_id":   l1_result["id"],
            "l1_confidence":     l1_result["confidence"],
            "l2_archetype_id":   l2_result["id"]         if l2_result else None,
            "l2_confidence":     l2_result["confidence"] if l2_result else 0.0,
            "matched_keystones": l2_result["matched_keystones"] if l2_result else [],
        }

    def classify_deck_id(self, deck_id: str) -> dict:
        """Look up a deck from the DB by public_id and classify it."""
        conn = psycopg2.connect(self.db_url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT c.card_name
                    FROM deck_cards dc
                    JOIN cards c ON c.id = dc.card_id
                    WHERE dc.deck_id = %s
                      AND dc.board   = 'mainboard'
                      AND c.type_line NOT LIKE '%%Land%%'
                """, (deck_id,))
                card_names = [row[0] for row in cur.fetchall()]
        finally:
            conn.close()

        if not card_names:
            return {"error": f"No mainboard cards found for deck {deck_id!r}"}

        return self.classify_cards(card_names)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _classify_l1(self, embedding: np.ndarray | None, card_set: set[str]) -> dict | None:
        candidates = [a for a in self._l1_archetypes if a["centroid"] is not None]
        if not candidates:
            return None

        if embedding is not None:
            sims = [(a, _cosine_sim(embedding, a["centroid"])) for a in candidates]
            best, confidence = max(sims, key=lambda x: x[1])
        else:
            # Fallback: pick largest archetype (no embedding available)
            best       = max(candidates, key=lambda a: a["member_count"])
            confidence = 0.0

        return {"id": best["id"], "confidence": round(confidence, 4)}

    def _classify_l2(
        self,
        l1_id: int,
        embedding: np.ndarray | None,
        card_set: set[str],
    ) -> dict | None:
        children = [a for a in self._l2_archetypes if a["parent_id"] == l1_id]
        if not children:
            return None

        # Keystone matching first — fast and interpretable
        best_match   = None
        best_matched = []
        for child in children:
            matched = self._match_keystones(card_set, child["keystones"])
            if len(matched) > len(best_matched):
                best_matched = matched
                best_match   = child

        if best_match:
            return {
                "id":                best_match["id"],
                "confidence":        round(len(best_matched) / max(1, len(best_match["keystones"])), 4),
                "matched_keystones": best_matched,
            }

        # Fallback: centroid similarity if embeddings available
        if embedding is not None:
            with_centroids = [c for c in children if c["centroid"] is not None]
            if with_centroids:
                sims   = [(c, _cosine_sim(embedding, c["centroid"])) for c in with_centroids]
                best_c, sim = max(sims, key=lambda x: x[1])
                return {"id": best_c["id"], "confidence": round(sim, 4), "matched_keystones": []}

        return None
