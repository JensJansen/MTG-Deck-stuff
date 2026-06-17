"""
PyTorch dataset for masked deck completion (MLM-style training).

Each call to __getitem__ draws a fresh random mask, so the model sees
different masks for the same deck across epochs without needing to
pre-generate masked copies.
"""
import os

import numpy as np
import torch
from torch.utils.data import Dataset

from config import MASK_IDX, MASK_PROB, MAX_SEQ_LEN, PAD_IDX


class DeckDataset(Dataset):
    """
    Wraps the preprocessed (sequences, slot_types) arrays produced by preprocess.py.

    Each item is a 4-tuple of tensors, all shape (MAX_SEQ_LEN,):
        masked_ids   int64  — card indices; commander and visible mainboard cards
                              are intact; randomly chosen mainboard cards → MASK_IDX
        slot_types   int64  — 1 for commander slots, 0 for mainboard / pad
        padding_mask bool   — True at PAD positions (passed to TransformerEncoder)
        labels       int64  — original card index at masked positions, -100 elsewhere
                              (-100 is ignored by nn.CrossEntropyLoss)
    """

    def __init__(
        self,
        sequences:  np.ndarray,   # (N, MAX_SEQ_LEN) uint16
        slot_types: np.ndarray,   # (N, MAX_SEQ_LEN) uint8
        mask_prob:  float = MASK_PROB,
    ) -> None:
        self.sequences  = sequences.astype(np.int64)
        self.slot_types = slot_types.astype(np.int64)
        self.mask_prob  = mask_prob

    def __len__(self) -> int:
        return len(self.sequences)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, ...]:
        ids        = self.sequences[idx].copy()
        slot_types = self.slot_types[idx].copy()

        # Only mainboard cards (slot_type=0) that are not PAD are mask candidates.
        is_mainboard = (slot_types == 0) & (ids != PAD_IDX)
        candidates   = np.where(is_mainboard)[0]

        labels = np.full(MAX_SEQ_LEN, -100, dtype=np.int64)

        if len(candidates) > 0:
            n_mask = max(1, int(len(candidates) * self.mask_prob))
            chosen = np.random.choice(candidates, size=n_mask, replace=False)
            labels[chosen] = ids[chosen]
            ids[chosen]    = MASK_IDX

        return (
            torch.from_numpy(ids),
            torch.from_numpy(slot_types),
            torch.from_numpy(ids == PAD_IDX),   # padding_mask
            torch.from_numpy(labels),
        )


def load_dataset(data_dir: str) -> DeckDataset:
    data = np.load(os.path.join(data_dir, "sequences.npz"))
    return DeckDataset(data["sequences"], data["slot_types"])
