"""
Build and persist the card vocabulary, color-identity masks, and per-color-group
card frequencies that power the uniqueness dial at inference time.

Run via preprocess.py — not invoked directly.

Saved files (in DATA_DIR):
    vocab.json        list[str]  — card names; array index = token id
    ci_masks.npy      (V,) int32 — color-identity bitmask per vocab entry
    frequencies.npy   (V, 32) float32 — fraction of decks (by color identity)
                                         that include each card
"""
import json
import os

import numpy as np
import psycopg2

from config import DATA_DIR, PAD_IDX, MASK_IDX, UNK_IDX

# Indices 0-2 are reserved for special tokens; must match config constants.
_SPECIAL_TOKENS = ["[PAD]", "[MASK]", "[UNK]"]

# Color identity bitmask: W=1 U=2 B=4 R=8 G=16  →  32 possible values (0-31)
_N_COLOR_GROUPS = 32


def build(db_url: str) -> tuple[list[str], np.ndarray, np.ndarray]:
    """
    Query the database and return:
        vocab       : list of card names (length V); vocab[0..2] are special tokens
        ci_masks    : int32 array (V,)      — color-identity mask per card
        frequencies : float32 array (V, 32) — card frequency within each color group
    """
    conn = psycopg2.connect(db_url)
    try:
        vocab, ci_masks, frequencies = _query(conn)
    finally:
        conn.close()
    return vocab, ci_masks, frequencies


def _query(conn) -> tuple[list[str], np.ndarray, np.ndarray]:
    with conn.cursor() as cur:
        # All unique non-land cards that appear in at least one done commander deck.
        cur.execute("""
            SELECT DISTINCT c.card_name, c.ci_mask
            FROM cards c
            JOIN deck_cards dc ON dc.card_id = c.id
            JOIN decks d       ON d.public_id = dc.deck_id
            WHERE d.format    = 'commander'
              AND d.status    = 'done'
              AND dc.board   IN ('mainboard', 'commanders')
              AND c.type_line NOT LIKE '%%Land%%'
            ORDER BY c.card_name
        """)
        card_rows = cur.fetchall()   # [(name, ci_mask), ...]

        # Total done commander decks per color-identity group.
        cur.execute("""
            SELECT color_mask, COUNT(*) AS n
            FROM decks
            WHERE format = 'commander' AND status = 'done'
            GROUP BY color_mask
        """)
        totals = {row[0]: row[1] for row in cur.fetchall()}

        # How often each non-land mainboard card appears in each color group.
        cur.execute("""
            SELECT c.card_name, d.color_mask, COUNT(*) AS n
            FROM deck_cards dc
            JOIN cards c ON c.id = dc.card_id
            JOIN decks d ON d.public_id = dc.deck_id
            WHERE d.format   = 'commander'
              AND d.status   = 'done'
              AND dc.board   = 'mainboard'
              AND c.type_line NOT LIKE '%%Land%%'
            GROUP BY c.card_name, d.color_mask
        """)
        freq_rows = cur.fetchall()   # [(name, color_mask, count), ...]

    vocab      = _SPECIAL_TOKENS + [name for name, _ in card_rows]
    name_to_idx = {name: i for i, name in enumerate(vocab)}
    V          = len(vocab)

    # Color-identity mask per vocab entry
    ci_masks = np.zeros(V, dtype=np.int32)
    for name, ci_mask in card_rows:
        ci_masks[name_to_idx[name]] = ci_mask

    # Frequency matrix: frequencies[i, g] = P(card i | color group g)
    frequencies = np.zeros((V, _N_COLOR_GROUPS), dtype=np.float32)
    for name, color_mask, count in freq_rows:
        idx = name_to_idx.get(name)
        if idx is None or not (0 <= color_mask < _N_COLOR_GROUPS):
            continue
        total = totals.get(color_mask, 1)
        frequencies[idx, color_mask] = count / total

    return vocab, ci_masks, frequencies


def save(
    vocab: list[str],
    ci_masks: np.ndarray,
    frequencies: np.ndarray,
    data_dir: str = DATA_DIR,
) -> None:
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "vocab.json"), "w") as f:
        json.dump(vocab, f)
    np.save(os.path.join(data_dir, "ci_masks.npy"), ci_masks)
    np.save(os.path.join(data_dir, "frequencies.npy"), frequencies)
    print(f"Vocabulary: {len(vocab):,} tokens  ({len(vocab) - 3:,} cards + 3 special)")


def load(data_dir: str = DATA_DIR) -> tuple[list[str], np.ndarray, np.ndarray]:
    with open(os.path.join(data_dir, "vocab.json")) as f:
        vocab = json.load(f)
    ci_masks    = np.load(os.path.join(data_dir, "ci_masks.npy"))
    frequencies = np.load(os.path.join(data_dir, "frequencies.npy"))
    return vocab, ci_masks, frequencies
