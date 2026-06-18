"""
One-time preprocessing: extract all done commander decks from Postgres and
save them as numpy arrays ready for training.

Run this before train.py:
    python preprocess.py

Reads DATABASE_URL from environment (or the .env file in src/distributed scraper/).
Produces in DATA_DIR:
    vocab.json        card name list (index = token id)
    ci_masks.npy      color-identity bitmask per vocab entry  (V,) int32
    frequencies.npy   card frequency per color-identity group (V, 32) float32
    sequences.npz     sequences + slot_types arrays           (N, MAX_SEQ_LEN) uint16/uint8
"""
import os
import sys
from pathlib import Path

import numpy as np
import psycopg2
from tqdm import tqdm

# Allow running from this directory without installing the package.
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1]))

from constants.env import load_env
import vocabulary
from config import (
    DATA_DIR,
    DATABASE_URL,
    MAX_MAINBOARD_SLOTS,
    MAX_SEQ_LEN,
    N_COMMANDER_SLOTS,
    PAD_IDX,
    UNK_IDX,
)

def _load_decks(conn) -> dict[str, dict]:
    """
    Stream all deck_cards rows for done commander decks.
    Returns {public_id: {"commanders": [name, ...], "mainboard": [name, ...]}}.
    """
    decks: dict[str, dict] = {}

    with conn.cursor(name="deck_stream", withhold=True) as cur:
        cur.itersize = 50_000
        cur.execute("""
            SELECT d.public_id, c.card_name, dc.board
            FROM decks d
            JOIN deck_cards dc ON dc.deck_id  = d.public_id
            JOIN cards c       ON c.id        = dc.card_id
            WHERE d.format    = 'commander'
              AND d.status    = 'done'
              AND dc.board   IN ('mainboard', 'commanders')
              AND c.type_line NOT LIKE '%%Land%%'
            ORDER BY d.public_id
        """)

        for public_id, card_name, board in tqdm(cur, desc="Streaming rows", unit=" rows"):
            if public_id not in decks:
                decks[public_id] = {"commanders": [], "mainboard": []}
            decks[public_id][board].append(card_name)

    return decks


def _encode(deck: dict, name_to_idx: dict) -> tuple[np.ndarray, np.ndarray] | None:
    commanders = deck["commanders"][:N_COMMANDER_SLOTS]
    mainboard  = deck["mainboard"][:MAX_MAINBOARD_SLOTS]

    # Skip decks with no commander or no mainboard cards.
    if not commanders or not mainboard:
        return None

    seq        = np.full(MAX_SEQ_LEN, PAD_IDX, dtype=np.uint16)
    slot_types = np.zeros(MAX_SEQ_LEN, dtype=np.uint8)

    for i, name in enumerate(commanders):
        seq[i]        = name_to_idx.get(name, UNK_IDX)
        slot_types[i] = 1

    for i, name in enumerate(mainboard):
        seq[N_COMMANDER_SLOTS + i] = name_to_idx.get(name, UNK_IDX)
        # slot_types stays 0 for mainboard positions

    return seq, slot_types


def main() -> None:
    load_env()

    db_url = os.environ.get("DATABASE_URL", DATABASE_URL)
    os.makedirs(DATA_DIR, exist_ok=True)

    print("── Step 1/3  Building vocabulary and frequencies ─────────────────")
    vocab, ci_masks, frequencies = vocabulary.build(db_url)
    vocabulary.save(vocab, ci_masks, frequencies, DATA_DIR)
    name_to_idx = {name: i for i, name in enumerate(vocab)}

    print("\n── Step 2/3  Loading decks from database ─────────────────────────")
    conn = psycopg2.connect(db_url)
    try:
        decks = _load_decks(conn)
    finally:
        conn.close()
    print(f"Loaded {len(decks):,} decks")

    print("\n── Step 3/3  Encoding decks ──────────────────────────────────────")
    seqs_list  = []
    slots_list = []
    skipped    = 0

    for deck in tqdm(decks.values(), desc="Encoding", unit=" decks"):
        result = _encode(deck, name_to_idx)
        if result is None:
            skipped += 1
            continue
        seqs_list.append(result[0])
        slots_list.append(result[1])

    sequences  = np.stack(seqs_list)
    slot_types = np.stack(slots_list)

    out_path = os.path.join(DATA_DIR, "sequences.npz")
    np.savez_compressed(out_path, sequences=sequences, slot_types=slot_types)

    seq_mb   = sequences.nbytes  / 1e6
    slot_mb  = slot_types.nbytes / 1e6
    print(f"\nSaved {len(sequences):,} decks → {out_path}")
    print(f"  sequences  {sequences.shape}  {seq_mb:.0f} MB")
    print(f"  slot_types {slot_types.shape}  {slot_mb:.0f} MB")
    if skipped:
        print(f"  Skipped {skipped:,} decks (no commander or empty mainboard)")


if __name__ == "__main__":
    main()
