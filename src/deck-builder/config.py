import os
import torch

# Database — read from environment so training and inference work identically
# on local machines and Fly.io without code changes.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:password@localhost/deckgen"
)

# Directories — override via env for Fly.io volume mounts
DATA_DIR       = os.environ.get("DATA_DIR",       "data")
CHECKPOINT_DIR = os.environ.get("CHECKPOINT_DIR", "checkpoints")

# ── Model ──────────────────────────────────────────────────────────────────────
EMBEDDING_DIM = 256
NUM_HEADS     = 8
NUM_LAYERS    = 6
FFN_DIM       = 1024
DROPOUT       = 0.1

# Sequence layout: [CMD_0, CMD_1, card, card, ..., PAD, PAD]
#   Positions 0-1 are commander slots (slot_type = 1, never masked).
#   Positions 2+ are non-land mainboard slots (slot_type = 0).
MAX_SEQ_LEN        = 100
N_COMMANDER_SLOTS  = 2
MAX_MAINBOARD_SLOTS = MAX_SEQ_LEN - N_COMMANDER_SLOTS

# Special token indices — must stay in sync with vocabulary.py
PAD_IDX  = 0   # padding / empty position
MASK_IDX = 1   # [MASK] token used during training and inference
UNK_IDX  = 2   # card not found in vocab

# ── Training ───────────────────────────────────────────────────────────────────
MASK_PROB    = 0.15   # fraction of mainboard cards to mask per deck
BATCH_SIZE   = 128
LR           = 1e-4
WEIGHT_DECAY = 0.01
WARMUP_STEPS = 2000
MAX_EPOCHS   = 30
GRAD_CLIP    = 1.0
SAVE_EVERY   = 1      # save checkpoint every N epochs
VAL_FRACTION = 0.02   # fraction of data held out for validation
VAL_MAX      = 10_000 # cap on validation set size

# ── Device ─────────────────────────────────────────────────────────────────────
DEVICE = (
    "cuda" if torch.cuda.is_available()
    else "mps" if torch.backends.mps.is_available()
    else "cpu"
)
