import os

# ── Database ───────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:password@localhost/deckgen"
)

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR         = os.environ.get("DATA_DIR",         "data")
DECK_BUILDER_DIR = os.environ.get("DECK_BUILDER_DIR", "../deck-builder")

# Path to a trained DeckTransformer checkpoint. Used by features.py to compute
# deck embeddings. Set to None to skip embedding-based features (uses card
# presence vectors only — less accurate but no model required).
MODEL_CHECKPOINT = os.environ.get("MODEL_CHECKPOINT", None)

# ── UMAP (Level 1 dimensionality reduction) ────────────────────────────────────
UMAP_N_COMPONENTS = 15
UMAP_N_NEIGHBORS  = 30
UMAP_MIN_DIST     = 0.0       # tighter clusters in embedding space
UMAP_METRIC       = "cosine"
UMAP_RANDOM_STATE = 42

# ── HDBSCAN Level 1 (coarse archetypes) ───────────────────────────────────────
# min_cluster_size is set dynamically as max(L1_MIN_CLUSTER_ABS, frac × n_decks)
L1_MIN_CLUSTER_FRAC = 0.001   # 0.1 % of format decks
L1_MIN_CLUSTER_ABS  = 50      # absolute floor regardless of format size
L1_MIN_SAMPLES      = 10      # controls cluster boundary conservatism
L1_CLUSTER_METHOD   = "eom"   # "eom" finds more varied cluster sizes than "leaf"

# ── Level 2 (sub-archetype detection) ─────────────────────────────────────────
# A Level 1 cluster is eligible for sub-splitting if enough of its cards have
# ambiguous presence rates (neither always-in nor always-out).
L2_VARIANCE_LO      = 0.20    # lower bound on card presence rate to count as high-variance
L2_VARIANCE_HI      = 0.80    # upper bound
L2_MIN_VARIABLE_CARDS = 10    # minimum number of high-variance cards to attempt split
L2_MIN_CLUSTER_FRAC = 0.05    # sub-cluster must be ≥ 5% of parent cluster
L2_MIN_CLUSTER_ABS  = 30      # absolute floor for sub-cluster size
L2_MIN_SAMPLES      = 5

# ── Keystone card rules ────────────────────────────────────────────────────────
KEYSTONE_P_IN   = 0.60   # card must appear in ≥ 60% of sub-archetype decks
KEYSTONE_P_OUT  = 0.15   # and in < 15% of all other sub-archetypes in the cluster
KEYSTONE_MAX    = 5      # max keystone cards to store per archetype

# ── Pip volume ─────────────────────────────────────────────────────────────────
PIP_COLORS = ["W", "U", "B", "R", "G"]

# ── CMC distribution ───────────────────────────────────────────────────────────
CMC_BINS = [0, 1, 2, 3, 4, 5, 6]   # last bin captures CMC ≥ 6
