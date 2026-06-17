"""
DeckRecommender — load a trained checkpoint and generate card recommendations.

Quickstart:
    from inference import DeckRecommender

    rec = DeckRecommender("checkpoints/epoch_030.pt")

    # Get the top-20 suggestions for the next card to add:
    suggestions = rec.recommend(
        commanders   = ["Yuriko, the Tiger's Shadow"],
        chosen_cards = ["Brainstorm", "Ponder", "Cyclonic Rift"],
        n_slots      = 30,      # how many empty slots remain
        dial         = 0.5,     # 0.0=staples fine  1.0=prefer unique cards
        top_k        = 20,
    )

    # Auto-fill an entire deck:
    full_deck = rec.complete_deck(
        commanders          = ["Yuriko, the Tiger's Shadow"],
        chosen_cards        = ["Brainstorm"],
        total_non_land_slots = 64,
        dial                = 0.3,
    )

The dial value maps to a log-frequency penalty:
    final_score = model_logit  −  dial × log(card_frequency_in_color_group + ε)

At dial=0 the model's learned co-occurrence signal is used as-is.
At dial=1 Sol Ring (≈80% of decks) is penalised ~4 nats relative to a card
that appears in 2% of decks, pushing unique synergy cards to the top.
Values above 1.0 are valid and amplify the uniqueness preference further.
"""
import sys
from pathlib import Path
from typing import Sequence

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parent))

import vocabulary
from config import (
    DATA_DIR,
    DEVICE,
    MASK_IDX,
    MAX_MAINBOARD_SLOTS,
    MAX_SEQ_LEN,
    N_COMMANDER_SLOTS,
    PAD_IDX,
    UNK_IDX,
)
from model import DeckTransformer


class DeckRecommender:
    def __init__(
        self,
        checkpoint_path: str,
        data_dir: str = DATA_DIR,
        device: str   = DEVICE,
    ) -> None:
        self.device = torch.device(device)

        self.vocab, self.ci_masks, self.frequencies = vocabulary.load(data_dir)
        self.name_to_idx = {name: i for i, name in enumerate(self.vocab)}

        ckpt = torch.load(checkpoint_path, map_location=self.device)
        self.model = DeckTransformer(len(self.vocab)).to(self.device)
        self.model.load_state_dict(ckpt["model"])
        self.model.eval()

    # ── Public API ────────────────────────────────────────────────────────────

    def recommend(
        self,
        commanders:   Sequence[str],
        chosen_cards: Sequence[str],
        n_slots:      int,
        dial:         float = 0.0,
        top_k:        int   = 20,
    ) -> list[tuple[str, float]]:
        """
        Return up to top_k (card_name, score) pairs for the next card(s) to add.
        Results are sorted best-first and exclude already-chosen cards, commanders,
        special tokens, and color-identity-illegal cards.
        """
        commander_ci = self._commander_ci(commanders)
        logits = self._forward(commanders, chosen_cards, n_slots)
        scores = self._apply_dial(logits, commander_ci, dial)
        scores = self._apply_hard_filters(scores, commander_ci, commanders, chosen_cards)

        top_indices = np.argsort(scores)[::-1]
        results = []
        for i in top_indices:
            if len(results) >= top_k:
                break
            if scores[i] == float("-inf"):
                break
            results.append((self.vocab[i], float(scores[i])))
        return results

    def complete_deck(
        self,
        commanders:           Sequence[str],
        chosen_cards:         Sequence[str],
        total_non_land_slots: int,
        dial:                 float = 0.0,
    ) -> list[str]:
        """
        Greedily fill the deck to total_non_land_slots non-land cards.
        Returns the full list of non-land mainboard cards (chosen + recommended).
        """
        deck = list(chosen_cards)
        while len(deck) < total_non_land_slots:
            remaining   = total_non_land_slots - len(deck)
            suggestions = self.recommend(commanders, deck, remaining, dial, top_k=1)
            if not suggestions:
                break
            deck.append(suggestions[0][0])
        return deck

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _commander_ci(self, commanders: Sequence[str]) -> int:
        """OR together the color-identity masks of all commanders."""
        ci = 0
        for name in commanders:
            idx = self.name_to_idx.get(name)
            if idx is not None:
                ci |= int(self.ci_masks[idx])
        return ci

    def _forward(
        self,
        commanders:   Sequence[str],
        chosen_cards: Sequence[str],
        n_slots:      int,
    ) -> np.ndarray:
        """
        Build the input sequence, run the transformer, and return the averaged
        logits over all MASK positions as a (V,) float32 numpy array.
        """
        seq        = np.full(MAX_SEQ_LEN, PAD_IDX, dtype=np.int64)
        slot_types = np.zeros(MAX_SEQ_LEN, dtype=np.int64)
        mask_positions = []

        # Commander slots (positions 0 and 1)
        for i, name in enumerate(list(commanders)[:N_COMMANDER_SLOTS]):
            seq[i]        = self.name_to_idx.get(name, UNK_IDX)
            slot_types[i] = 1

        # Visible chosen cards
        offset   = N_COMMANDER_SLOTS
        n_chosen = min(len(chosen_cards), MAX_MAINBOARD_SLOTS)
        for i, name in enumerate(list(chosen_cards)[:n_chosen]):
            seq[offset + i] = self.name_to_idx.get(name, UNK_IDX)

        # MASK tokens for empty slots
        mask_start = offset + n_chosen
        n_masks    = min(n_slots, MAX_SEQ_LEN - mask_start)
        for i in range(n_masks):
            pos = mask_start + i
            seq[pos] = MASK_IDX
            mask_positions.append(pos)

        pad_mask   = torch.tensor(seq == PAD_IDX, dtype=torch.bool).unsqueeze(0).to(self.device)
        input_tens = torch.tensor(seq,        dtype=torch.long).unsqueeze(0).to(self.device)
        slot_tens  = torch.tensor(slot_types, dtype=torch.long).unsqueeze(0).to(self.device)

        with torch.no_grad():
            logits = self.model(input_tens, slot_tens, pad_mask)   # (1, L, V)

        # Average logits over all MASK positions for a single score vector.
        positions = mask_positions if mask_positions else [mask_start]
        avg_logits = logits[0, positions, :].mean(dim=0)
        return avg_logits.cpu().float().numpy()

    def _apply_dial(
        self,
        logits:       np.ndarray,
        commander_ci: int,
        dial:         float,
    ) -> np.ndarray:
        """
        Subtract dial × log(frequency) from each card's logit.
        Cards with dial=0 are ranked purely by model confidence.
        Cards with high dial values are penalised proportionally to how
        commonly they appear in decks of this color identity.
        """
        if dial == 0.0 or not (0 <= commander_ci < 32):
            return logits
        freq    = self.frequencies[:, commander_ci]        # (V,)
        penalty = dial * np.log(freq + 1e-9)
        return logits - penalty

    def _apply_hard_filters(
        self,
        scores:       np.ndarray,
        commander_ci: int,
        commanders:   Sequence[str],
        chosen_cards: Sequence[str],
    ) -> np.ndarray:
        """
        Set score to -inf for:
          - Special tokens (PAD, MASK, UNK)
          - Cards already in the deck or in the commander slot
          - Cards whose color identity is not a subset of the commander's
        """
        scores  = scores.copy()
        exclude = set(commanders) | set(chosen_cards)

        for i, name in enumerate(self.vocab):
            if i < 3:                          # special tokens
                scores[i] = float("-inf")
                continue
            if name in exclude:
                scores[i] = float("-inf")
                continue
            card_ci = int(self.ci_masks[i])
            if (card_ci & commander_ci) != card_ci:    # color-identity violation
                scores[i] = float("-inf")

        return scores
