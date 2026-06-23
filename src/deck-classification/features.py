"""
Feature extraction for deck classification.

Three feature sets are computed per deck and cached to DATA_DIR:

  embeddings_{format}.npz
      deck_ids   : (N,) str
      embeddings : (N, D) float32  — mean of DeckTransformer card embeddings

  features_{format}.npz
      pip_volumes : (N, 5) float32  — L2-normalised pip counts [W,U,B,R,G]
      cmc_dists   : (N, 7) float32  — proportion of cards at CMC 0-5 and 6+

  presence_{format}.npz   (sparse, stored as COO arrays)
      data / indices / indptr / shape  — scipy csr_matrix (N, V) bool

Embeddings require a trained DeckTransformer checkpoint (MODEL_CHECKPOINT in
config.py). If the checkpoint is absent the embedding step is skipped and
Level 1 clustering falls back to the card presence matrix directly.
"""
import array
import os
import re
import sys
import uuid
from pathlib import Path

import numpy as np
import psycopg2
import scipy.sparse as sp
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    CMC_BINS,
    DATA_DIR,
    DATABASE_URL,
    DECK_BUILDER_DIR,
    MODEL_CHECKPOINT,
    PIP_COLORS,
)

# Import DeckTransformer and vocabulary from the sibling deck-builder package.
sys.path.insert(0, str(Path(__file__).parents[1] / "deck-builder"))

_PIP_RE = re.compile(r'\{([WUBRG])\}')

_FORMAT_TABLE: dict[str, str] = {"highlanderCanadian": "canadian_highlander"}
_SINGLETON_FORMATS = frozenset(["commander", "highlanderCanadian"])


def _table_prefix(fmt: str) -> str:
    return _FORMAT_TABLE.get(fmt, fmt)


# ── Pip volume ─────────────────────────────────────────────────────────────────

def _parse_pips(mana_cost: str | None) -> list[int]:
    """Return [W, U, B, R, G] pip counts from a mana cost string like '{2}{R}{R}'."""
    if not mana_cost:
        return [0, 0, 0, 0, 0]
    counts = {c: 0 for c in PIP_COLORS}
    for pip in _PIP_RE.findall(mana_cost):
        counts[pip] += 1
    return [counts[c] for c in PIP_COLORS]


# ── Database queries ───────────────────────────────────────────────────────────

def load_deck_ids(conn, fmt: str) -> list[str]:
    prefix = _table_prefix(fmt)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT public_id FROM {prefix}_decks WHERE status = 'done' ORDER BY public_id",
        )
        return [row[0] for row in cur.fetchall()]


def _load_card_data(conn, fmt: str) -> dict[str, dict]:
    """
    Returns {card_name: {mana_cost, cmc}} for all non-land cards
    appearing in done decks of the given format.
    """
    prefix = _table_prefix(fmt)
    if fmt in _SINGLETON_FORMATS:
        sql = f"""
            SELECT DISTINCT c.card_name, c.mana_cost, c.cmc
            FROM {prefix}_decks d
            CROSS JOIN LATERAL jsonb_array_elements(d.cards) AS elem
            JOIN cards c ON c.card_name = elem->>'card_name'
            WHERE d.status  = 'done'
              AND d.cards   IS NOT NULL
              AND c.type_line NOT LIKE '%%Land%%'
        """
    else:
        sql = f"""
            SELECT DISTINCT c.card_name, c.mana_cost, c.cmc
            FROM cards c
            JOIN {prefix}_deck_cards dc ON dc.card_id  = c.id
            JOIN {prefix}_decks      d  ON d.public_id = dc.deck_id
            WHERE d.status  = 'done'
              AND dc.board  = 'mainboard'
              AND c.type_line NOT LIKE '%%Land%%'
        """
    with conn.cursor() as cur:
        cur.execute(sql)
        return {row[0]: {"mana_cost": row[1], "cmc": row[2] or 0.0} for row in cur.fetchall()}


def _stream_deck_cards(conn, fmt: str):
    """
    Yields (public_id, card_name) for all non-land mainboard cards in done
    decks of the given format, ordered by public_id.
    """
    prefix = _table_prefix(fmt)
    if fmt in _SINGLETON_FORMATS:
        sql = f"""
            SELECT d.public_id, c.card_name
            FROM {prefix}_decks d
            CROSS JOIN LATERAL jsonb_array_elements(d.cards) AS elem
            JOIN cards c ON c.card_name = elem->>'card_name'
            WHERE d.status  = 'done'
              AND d.cards   IS NOT NULL
              AND c.type_line NOT LIKE '%%Land%%'
            ORDER BY d.public_id
        """
    else:
        sql = f"""
            SELECT d.public_id, c.card_name
            FROM {prefix}_decks      d
            JOIN {prefix}_deck_cards dc ON dc.deck_id  = d.public_id
            JOIN cards               c  ON c.id        = dc.card_id
            WHERE d.status  = 'done'
              AND dc.board  = 'mainboard'
              AND c.type_line NOT LIKE '%%Land%%'
            ORDER BY d.public_id
        """
    with conn.cursor(name=f"card_stream_{uuid.uuid4().hex[:8]}", withhold=True) as cur:
        cur.itersize = 100_000
        cur.execute(sql)
        yield from cur


def _stream_deck_cards_with_qty(conn, fmt: str):
    """
    Yields (public_id, card_name, quantity) for all non-land mainboard cards.
    Singleton formats always yield quantity=1.
    """
    prefix = _table_prefix(fmt)
    if fmt in _SINGLETON_FORMATS:
        sql = f"""
            SELECT d.public_id, c.card_name, 1
            FROM {prefix}_decks d
            CROSS JOIN LATERAL jsonb_array_elements(d.cards) AS elem
            JOIN cards c ON c.card_name = elem->>'card_name'
            WHERE d.status  = 'done'
              AND d.cards   IS NOT NULL
              AND c.type_line NOT LIKE '%%Land%%'
            ORDER BY d.public_id
        """
    else:
        sql = f"""
            SELECT d.public_id, c.card_name, dc.quantity
            FROM {prefix}_decks      d
            JOIN {prefix}_deck_cards dc ON dc.deck_id  = d.public_id
            JOIN cards               c  ON c.id        = dc.card_id
            WHERE d.status  = 'done'
              AND dc.board  = 'mainboard'
              AND c.type_line NOT LIKE '%%Land%%'
            ORDER BY d.public_id
        """
    with conn.cursor(name=f"card_stream_qty_{uuid.uuid4().hex[:8]}", withhold=True) as cur:
        cur.itersize = 100_000
        cur.execute(sql)
        yield from cur


# ── Feature computation ────────────────────────────────────────────────────────

def compute_structural_features(
    deck_ids: list[str],
    conn,
    fmt: str,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns:
        pip_volumes : (N, 5) float32 — L2-normalised pip counts per deck
        cmc_dists   : (N, 7) float32 — normalised CMC histogram per deck
    """
    card_data  = _load_card_data(conn, fmt)
    id_to_idx  = {did: i for i, did in enumerate(deck_ids)}
    N          = len(deck_ids)
    n_cmc_bins = len(CMC_BINS) + 1      # bins 0..max + overflow

    pip_sums  = np.zeros((N, len(PIP_COLORS)), dtype=np.float32)
    cmc_sums  = np.zeros((N, n_cmc_bins),      dtype=np.float32)

    print("  Computing pip volumes and CMC distributions...")
    for deck_id, card_name in tqdm(_stream_deck_cards(conn, fmt), desc="  Structural"):
        idx = id_to_idx.get(deck_id)
        if idx is None:
            continue
        info = card_data.get(card_name)
        if info is None:
            continue

        pip_sums[idx] += _parse_pips(info["mana_cost"])

        cmc = int(info["cmc"])
        bin_idx = min(cmc, n_cmc_bins - 1)
        cmc_sums[idx, bin_idx] += 1

    # L2-normalise pip volumes (treat all-zero as zero vector)
    norms = np.linalg.norm(pip_sums, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    pip_volumes = pip_sums / norms

    # Normalise CMC distributions to sum to 1
    cmc_totals = cmc_sums.sum(axis=1, keepdims=True)
    cmc_totals[cmc_totals == 0] = 1.0
    cmc_dists = cmc_sums / cmc_totals

    return pip_volumes.astype(np.float32), cmc_dists.astype(np.float32)


def compute_card_presence(
    deck_ids: list[str],
    conn,
    fmt: str,
) -> tuple[sp.csr_matrix, sp.csr_matrix, list[str]]:
    """
    Returns:
        presence_matrix : scipy csr_matrix (N, V) bool — 1 if card present
        count_matrix    : scipy csr_matrix (N, V) float32 — actual copy count
        card_vocab      : list[str] of length V — card names in column order
    """
    id_to_row  = {did: i for i, did in enumerate(deck_ids)}
    card_index: dict[str, int] = {}

    rows  = array.array('i')
    cols  = array.array('i')
    qtys  = array.array('f')

    print("  Computing card presence matrix...")
    for deck_id, card_name, qty in tqdm(_stream_deck_cards_with_qty(conn, fmt), desc="  Presence"):
        row = id_to_row.get(deck_id)
        if row is None:
            continue
        if card_name not in card_index:
            card_index[card_name] = len(card_index)
        col = card_index[card_name]
        rows.append(row)
        cols.append(col)
        qtys.append(float(qty))

    N = len(deck_ids)
    V = len(card_index)

    bool_data = np.ones(len(rows), dtype=np.bool_)
    presence  = sp.csr_matrix((bool_data, (rows, cols)), shape=(N, V))

    qty_data  = np.array(qtys, dtype=np.float32)
    counts    = sp.csr_matrix((qty_data, (rows, cols)), shape=(N, V))

    card_vocab = [None] * V
    for name, idx in card_index.items():
        card_vocab[idx] = name

    return presence, counts, card_vocab


def compute_embeddings(
    deck_ids: list[str],
    conn,
    fmt: str,
    checkpoint_path: str,
) -> np.ndarray:
    """
    Returns (N, EMBEDDING_DIM) float32 array — mean card embedding per deck.
    Requires a trained DeckTransformer checkpoint.
    """
    import torch
    import vocabulary as voc
    from model import DeckTransformer
    from config import DECK_BUILDER_DIR

    builder_data = os.path.join(DECK_BUILDER_DIR, "data")
    vocab, _, _  = voc.load(builder_data)
    name_to_idx  = {name: i for i, name in enumerate(vocab)}

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    ckpt   = torch.load(checkpoint_path, map_location=device)
    model  = DeckTransformer(len(vocab)).to(device)
    model.load_state_dict(ckpt["model"])
    model.eval()

    # Extract the card embedding weight matrix — shape (V, D)
    card_emb = model.card_embedding.weight.detach().cpu().numpy()
    EMBEDDING_DIM = card_emb.shape[1]

    id_to_row = {did: i for i, did in enumerate(deck_ids)}
    N = len(deck_ids)
    sums  = np.zeros((N, EMBEDDING_DIM), dtype=np.float32)
    counts = np.zeros(N, dtype=np.int32)

    print("  Computing deck embeddings...")
    for deck_id, card_name in tqdm(_stream_deck_cards(conn, fmt), desc="  Embeddings"):
        row = id_to_row.get(deck_id)
        if row is None:
            continue
        token_idx = name_to_idx.get(card_name)
        if token_idx is None:
            continue
        sums[row]  += card_emb[token_idx]
        counts[row] += 1

    counts[counts == 0] = 1
    return sums / counts[:, None]


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_path(fmt: str, suffix: str) -> str:
    return os.path.join(DATA_DIR, f"{suffix}_{fmt}.npz")


def save_structural(fmt: str, deck_ids: list[str], pip_volumes: np.ndarray, cmc_dists: np.ndarray) -> None:
    np.savez_compressed(
        _cache_path(fmt, "features"),
        deck_ids=np.array(deck_ids),
        pip_volumes=pip_volumes,
        cmc_dists=cmc_dists,
    )


def load_structural(fmt: str) -> tuple[list[str], np.ndarray, np.ndarray]:
    data = np.load(_cache_path(fmt, "features"), allow_pickle=True)
    return list(data["deck_ids"]), data["pip_volumes"], data["cmc_dists"]


def save_presence(fmt: str, deck_ids: list[str], matrix: sp.csr_matrix, counts: sp.csr_matrix, card_vocab: list[str]) -> None:
    np.savez_compressed(
        _cache_path(fmt, "presence"),
        deck_ids=np.array(deck_ids),
        card_vocab=np.array(card_vocab),
        data=matrix.data,
        indices=matrix.indices,
        indptr=matrix.indptr,
        shape=np.array(matrix.shape),
        count_data=counts.data,
        count_indices=counts.indices,
        count_indptr=counts.indptr,
    )


def load_presence(fmt: str) -> tuple[list[str], sp.csr_matrix, sp.csr_matrix | None, list[str]]:
    d   = np.load(_cache_path(fmt, "presence"), allow_pickle=True)
    mat = sp.csr_matrix((d["data"], d["indices"], d["indptr"]), shape=tuple(d["shape"]))
    if "count_data" in d:
        counts = sp.csr_matrix(
            (d["count_data"], d["count_indices"], d["count_indptr"]),
            shape=tuple(d["shape"]),
        )
    else:
        counts = None  # old cache without quantity data
    return list(d["deck_ids"]), mat, counts, list(d["card_vocab"])


def save_embeddings(fmt: str, deck_ids: list[str], embeddings: np.ndarray) -> None:
    np.savez_compressed(
        _cache_path(fmt, "embeddings"),
        deck_ids=np.array(deck_ids),
        embeddings=embeddings,
    )


def load_embeddings(fmt: str) -> tuple[list[str], np.ndarray]:
    data = np.load(_cache_path(fmt, "embeddings"), allow_pickle=True)
    return list(data["deck_ids"]), data["embeddings"]


def cache_exists(fmt: str) -> dict[str, bool]:
    return {
        "embeddings": os.path.exists(_cache_path(fmt, "embeddings")),
        "features":   os.path.exists(_cache_path(fmt, "features")),
        "presence":   os.path.exists(_cache_path(fmt, "presence")),
    }
