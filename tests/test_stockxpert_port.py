from __future__ import annotations

import math
import unittest

import pandas as pd
import torch

from models.stockxpert_port.source.stockxpert.v3.models.stockxpert import (
    StockXpertModelV3,
)


class StockXpertPortTest(unittest.TestCase):
    def test_centered_horizon_one_target_changes_with_known_prior_move(self) -> None:
        # Synthetic log closes only. At t=2, a centered width-three mean of
        # h=1 raw returns reads [t-1, t, t+1].
        closes = [0.0, 0.1, 0.3, 0.6, 0.8, 1.0]

        def centered_target(values: list[float], t: int) -> float:
            log_close = pd.Series(values, dtype="float64")
            raw_dlog = log_close.shift(-1) - log_close
            smoothed = raw_dlog.rolling(3, center=True).mean().fillna(raw_dlog)
            return float(smoothed.iloc[t])

        original = centered_target(closes, 2)
        changed = closes.copy()
        changed[1] += 0.3  # perturb only the already-known close at t-1
        perturbed = centered_target(changed, 2)

        self.assertAlmostEqual(original, (0.2 + 0.3 + 0.2) / 3.0)
        self.assertAlmostEqual(perturbed, (-0.1 + 0.3 + 0.2) / 3.0)
        self.assertNotAlmostEqual(original, perturbed)
        self.assertEqual(closes[2:], changed[2:])

    def test_model_forward_and_backward_on_dummy_cpu_inputs(self) -> None:
        torch.set_num_threads(2)
        torch.manual_seed(8)
        model = StockXpertModelV3(
            short_dim=8,
            mid_dim=12,
            long_dim=8,
            context_dim=24,
            sentiment_dim=9,
            hidden_dim=32,
            stock_embed_dim=8,
            stock_traits_dim=7,
            attn_heads=4,
            bottleneck_dim=16,
            dropout=0.0,
        ).cpu()
        model.train()

        inputs = (
            torch.randn(2, 7, 8),
            torch.randn(2, 21, 12),
            torch.randn(2, 60, 8),
            torch.randn(2, 24),
            torch.randn(2, 9),
            torch.randn(2, 7),
        )
        outputs = model(*inputs)
        direction, magnitude, confidence, zones, aux, levels, _ = outputs
        self.assertEqual(direction.shape, (2, 5))
        self.assertEqual(magnitude.shape, (2, 5))
        self.assertEqual(confidence.shape, (2, 5))
        self.assertEqual(zones.shape, (2, 5, 7))
        self.assertEqual(levels.shape, (2, 5, 3))

        loss = (
            direction.square().mean()
            + magnitude.square().mean()
            + confidence.square().mean()
            + zones.square().mean()
            + levels.square().mean()
            + aux.square().mean()
        )
        loss.backward()

        gradients = [
            parameter.grad
            for parameter in model.parameters()
            if parameter.requires_grad
        ]
        self.assertTrue(gradients)
        populated_gradients = [gradient for gradient in gradients if gradient is not None]
        self.assertTrue(populated_gradients)
        self.assertTrue(
            all(torch.isfinite(gradient).all().item() for gradient in populated_gradients)
        )
        self.assertTrue(
            any(torch.count_nonzero(gradient).item() for gradient in populated_gradients)
        )
        for component in (
            "short_encoder",
            "mid_encoder",
            "long_encoder",
            "context_encoder",
            "sentiment_encoder",
            "fusion",
            "mixer",
        ):
            self.assertTrue(
                any(
                    parameter.grad is not None
                    for name, parameter in model.named_parameters()
                    if name.startswith(component)
                ),
                component,
            )
        self.assertTrue(math.isfinite(loss.item()))


if __name__ == "__main__":
    unittest.main()
