"""Retrieval quality measures and a self-retrieval check. Binary relevance throughout."""
from __future__ import annotations

import math
import random
from typing import Dict, Iterable, List, Sequence, Set


def recall_at_k(ranked: Sequence[str], relevant: Set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def precision_at_k(ranked: Sequence[str], relevant: Set[str], k: int) -> float:
    top = ranked[:k]
    return sum(1 for r in top if r in relevant) / k if k else 0.0


def reciprocal_rank(ranked: Sequence[str], relevant: Set[str]) -> float:
    for pos, r in enumerate(ranked, start=1):
        if r in relevant:
            return 1.0 / pos
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: Set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(pos + 1) for pos, r in enumerate(ranked[:k], start=1) if r in relevant)
    ideal = sum(1.0 / math.log2(pos + 1) for pos in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def summarise(runs: Iterable[Dict[str, Sequence[str]]], ks=(1, 5, 10)) -> Dict[str, float]:
    """runs: [{'ranked': [...], 'relevant': {...}}]"""
    runs = list(runs)
    n = len(runs)
    if not n:
        return {"queries": 0}
    out: Dict[str, float] = {"queries": n}
    for k in ks:
        out[f"recall@{k}"] = round(sum(recall_at_k(r["ranked"], set(r["relevant"]), k) for r in runs) / n, 4)
    out["precision@5"] = round(sum(precision_at_k(r["ranked"], set(r["relevant"]), 5) for r in runs) / n, 4)
    out["mrr"] = round(sum(reciprocal_rank(r["ranked"], set(r["relevant"])) for r in runs) / n, 4)
    out["ndcg@10"] = round(sum(ndcg_at_k(r["ranked"], set(r["relevant"]), 10) for r in runs) / n, 4)
    return out


def pick_chunks(count: int, n: int, seed: int = 7) -> List[int]:
    rng = random.Random(seed)
    return sorted(rng.sample(range(count), min(n, count)))
