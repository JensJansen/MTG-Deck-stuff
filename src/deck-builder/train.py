"""
Train the DeckTransformer.

    python train.py                          # fresh run
    python train.py --resume checkpoints/epoch_005.pt   # resume

Checkpoints are saved to CHECKPOINT_DIR after every epoch (configurable via
SAVE_EVERY in config.py). Each checkpoint contains model weights, optimizer
state, and scheduler state so training can be resumed exactly.
"""
import argparse
import math
import os
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split

sys.path.insert(0, str(Path(__file__).parent))

import dataset as ds
import vocabulary
from config import (
    BATCH_SIZE,
    CHECKPOINT_DIR,
    DATA_DIR,
    DEVICE,
    GRAD_CLIP,
    LR,
    MAX_EPOCHS,
    SAVE_EVERY,
    VAL_FRACTION,
    VAL_MAX,
    WARMUP_STEPS,
    WEIGHT_DECAY,
)
from model import DeckTransformer


# ── Learning rate schedule: linear warmup then cosine decay ───────────────────

def _lr_lambda(step: int, warmup: int, total: int) -> float:
    if step < warmup:
        return step / max(1, warmup)
    progress = (step - warmup) / max(1, total - warmup)
    return 0.5 * (1.0 + math.cos(math.pi * progress))


# ── Train / eval loops ────────────────────────────────────────────────────────

def _train_epoch(model, loader, optimizer, scheduler, criterion, device) -> float:
    model.train()
    total_loss = total_tokens = 0

    for input_ids, slot_types, pad_mask, labels in loader:
        input_ids  = input_ids.to(device)
        slot_types = slot_types.to(device)
        pad_mask   = pad_mask.to(device)
        labels     = labels.to(device)

        logits = model(input_ids, slot_types, pad_mask)           # (B, L, V)
        loss   = criterion(logits.view(-1, logits.size(-1)), labels.view(-1))

        optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP)
        optimizer.step()
        scheduler.step()

        n            = (labels != -100).sum().item()
        total_loss  += loss.item() * n
        total_tokens += n

    return total_loss / max(1, total_tokens)


@torch.no_grad()
def _eval_epoch(model, loader, criterion, device) -> float:
    model.eval()
    total_loss = total_tokens = 0

    for input_ids, slot_types, pad_mask, labels in loader:
        input_ids  = input_ids.to(device)
        slot_types = slot_types.to(device)
        pad_mask   = pad_mask.to(device)
        labels     = labels.to(device)

        logits = model(input_ids, slot_types, pad_mask)
        loss   = criterion(logits.view(-1, logits.size(-1)), labels.view(-1))

        n            = (labels != -100).sum().item()
        total_loss  += loss.item() * n
        total_tokens += n

    return total_loss / max(1, total_tokens)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", default=None, metavar="CKPT",
                        help="Checkpoint path to resume from")
    parser.add_argument("--data-dir",  default=DATA_DIR)
    parser.add_argument("--ckpt-dir",  default=CHECKPOINT_DIR)
    args = parser.parse_args()

    os.makedirs(args.ckpt_dir, exist_ok=True)
    device = torch.device(DEVICE)
    print(f"Device: {device}")

    # ── Data ──────────────────────────────────────────────────────────────────
    vocab, _, _ = vocabulary.load(args.data_dir)
    vocab_size  = len(vocab)

    full_set  = ds.load_dataset(args.data_dir)
    val_size  = min(VAL_MAX, int(VAL_FRACTION * len(full_set)))
    train_size = len(full_set) - val_size
    train_set, val_set = random_split(full_set, [train_size, val_size],
                                      generator=torch.Generator().manual_seed(42))

    n_workers    = min(4, os.cpu_count() or 1)
    train_loader = DataLoader(train_set, batch_size=BATCH_SIZE, shuffle=True,
                              num_workers=n_workers, pin_memory=device.type == "cuda")
    val_loader   = DataLoader(val_set,   batch_size=BATCH_SIZE, shuffle=False,
                              num_workers=max(1, n_workers // 2), pin_memory=device.type == "cuda")

    print(f"Train: {train_size:,}  Val: {val_size:,}  Vocab: {vocab_size:,}")

    # ── Model, optimiser, scheduler ───────────────────────────────────────────
    model     = DeckTransformer(vocab_size).to(device)
    n_params  = sum(p.numel() for p in model.parameters())
    print(f"Parameters: {n_params:,}")

    optimizer   = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    total_steps = len(train_loader) * MAX_EPOCHS
    scheduler   = torch.optim.lr_scheduler.LambdaLR(
        optimizer,
        lambda step: _lr_lambda(step, WARMUP_STEPS, total_steps),
    )
    criterion = nn.CrossEntropyLoss(ignore_index=-100)

    # ── Optional resume ───────────────────────────────────────────────────────
    start_epoch = 1
    if args.resume:
        ckpt = torch.load(args.resume, map_location=device)
        model.load_state_dict(ckpt["model"])
        optimizer.load_state_dict(ckpt["optimizer"])
        scheduler.load_state_dict(ckpt["scheduler"])
        start_epoch = ckpt["epoch"] + 1
        print(f"Resumed from {args.resume}  (epoch {ckpt['epoch']}  val={ckpt['val_loss']:.4f})")

    # ── Training loop ─────────────────────────────────────────────────────────
    best_val = float("inf")

    for epoch in range(start_epoch, MAX_EPOCHS + 1):
        t0         = time.time()
        train_loss = _train_epoch(model, train_loader, optimizer, scheduler, criterion, device)
        val_loss   = _eval_epoch(model, val_loader, criterion, device)
        elapsed    = time.time() - t0
        lr_now     = scheduler.get_last_lr()[0]
        marker     = " *" if val_loss < best_val else ""
        best_val   = min(best_val, val_loss)

        print(
            f"Epoch {epoch:3d}/{MAX_EPOCHS}"
            f"  train={train_loss:.4f}"
            f"  val={val_loss:.4f}{marker}"
            f"  lr={lr_now:.2e}"
            f"  {elapsed:.0f}s"
        )

        if epoch % SAVE_EVERY == 0:
            ckpt_path = os.path.join(args.ckpt_dir, f"epoch_{epoch:03d}.pt")
            torch.save({
                "epoch":      epoch,
                "model":      model.state_dict(),
                "optimizer":  optimizer.state_dict(),
                "scheduler":  scheduler.state_dict(),
                "val_loss":   val_loss,
                "vocab_size": vocab_size,
            }, ckpt_path)
            print(f"  → saved {ckpt_path}")


if __name__ == "__main__":
    main()
