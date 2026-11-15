from __future__ import annotations

import ast
import math
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import torch

from models.stockxpert_port.source.stockxpert.v3.models.stockxpert import (
    StockXpertModelV3,
)


class StockXpertPortTest(unittest.TestCase):
    def test_centered_horizon_one_target_changes_with_known_prior_move(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "models/stockxpert_port/source/stockxpert/features/builder.py"
        )
        module = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        builder = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "FeatureBuilder"
        )
        method = next(
            node for node in builder.body
            if isinstance(node, ast.FunctionDef) and node.name == "_compute_targets"
        )
        isolated = ast.Module(body=[method], type_ignores=[])
        namespace = {"np": np, "pd": pd}
        exec(compile(isolated, str(source), "exec", dont_inherit=True), namespace)
        compute_targets = namespace["_compute_targets"]
        prices = np.exp(np.arange(30, dtype="float64") * 0.02)
        frame = pd.DataFrame({
            "Close": prices,
            "High": prices * 1.01,
            "Low": prices * 0.99,
            "log_return": [0.02, -0.02] * 15,
        })
        sample = 15
        changed = frame.copy()
        changed.loc[sample - 1, "Close"] *= np.exp(0.03)
        original = compute_targets(SimpleNamespace(horizons=[1]), frame)
        perturbed = compute_targets(SimpleNamespace(horizons=[1]), changed)

        self.assertAlmostEqual(
            original.loc[sample, "target_h1"] * original.loc[sample, "vol_ref"],
            0.02,
        )
        self.assertAlmostEqual(
            perturbed.loc[sample, "target_h1"] * perturbed.loc[sample, "vol_ref"],
            0.01,
        )
        self.assertNotAlmostEqual(
            original.loc[sample, "target_h1"], perturbed.loc[sample, "target_h1"]
        )
        pd.testing.assert_frame_equal(frame.iloc[sample:], changed.iloc[sample:])
        pd.testing.assert_series_equal(original["vol_ref"], perturbed["vol_ref"])

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
