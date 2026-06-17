"""
DeckTransformer — encoder-only transformer for masked deck completion.

Sequence layout fed to the model:
    [ CMD_0 | CMD_1 | card | card | ... | MASK | MASK | ... | PAD | PAD ]
      ─────────────  ─────────────────────────────────────────────────────
      commander        mainboard (visible or masked)     padding

slot_type tensor mirrors this:
    [ 1 | 1 | 0 | 0 | ... | 0 | 0 | ... | 0 | 0 ]
      commanders           mainboard               (PAD positions are 0 too;
                                                    the padding_mask silences them)

Design notes:
- No positional encoding: a deck is an unordered set, so position is meaningless.
  The slot_type embedding provides the only structural signal (commander vs mainboard).
- Weight tying between the input card embedding and the output projection keeps the
  parameter count low and is standard practice for masked language models.
- Pre-norm (norm_first=True) is used in each TransformerEncoderLayer for training
  stability without requiring careful learning-rate warmup tuning.
"""
import torch
import torch.nn as nn

from config import DROPOUT, EMBEDDING_DIM, FFN_DIM, NUM_HEADS, NUM_LAYERS, PAD_IDX


class DeckTransformer(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        embedding_dim: int = EMBEDDING_DIM,
        num_heads: int     = NUM_HEADS,
        num_layers: int    = NUM_LAYERS,
        ffn_dim: int       = FFN_DIM,
        dropout: float     = DROPOUT,
    ) -> None:
        super().__init__()

        self.card_embedding = nn.Embedding(vocab_size, embedding_dim, padding_idx=PAD_IDX)
        # Informs every attention head whether a card is a commander or a mainboard slot.
        self.slot_type_embedding = nn.Embedding(2, embedding_dim)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=embedding_dim,
            nhead=num_heads,
            dim_feedforward=ffn_dim,
            dropout=dropout,
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            encoder_layer,
            num_layers=num_layers,
            enable_nested_tensor=False,
        )

        self.output_head = nn.Linear(embedding_dim, vocab_size, bias=False)
        # Tie weights: the model uses the same geometry to encode and predict cards.
        self.output_head.weight = self.card_embedding.weight

        self._init_weights()

    def _init_weights(self) -> None:
        nn.init.normal_(self.card_embedding.weight,    std=0.02)
        nn.init.normal_(self.slot_type_embedding.weight, std=0.02)

    def forward(
        self,
        input_ids:    torch.Tensor,   # (B, L) int64
        slot_types:   torch.Tensor,   # (B, L) int64   0=mainboard  1=commander
        padding_mask: torch.Tensor,   # (B, L) bool    True = ignore this position
    ) -> torch.Tensor:                # (B, L, V) float32
        x = self.card_embedding(input_ids) + self.slot_type_embedding(slot_types)
        x = self.encoder(x, src_key_padding_mask=padding_mask)
        return self.output_head(x)
