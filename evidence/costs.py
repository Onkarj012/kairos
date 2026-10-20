"""Full Indian equity transaction cost model.

The model is intentionally the only supported backtest cost path. It includes
brokerage, STT, stamp duty, exchange transaction charges, GST on brokerage plus
exchange charges, SEBI fees, and adverse slippage by liquidity bucket.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProductType = Literal["delivery", "intraday"]
Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class IndianEquityCostModel:
    brokerage_rate: float = 0.0005
    brokerage_cap_per_order: float | None = 20.0
    stt_delivery_sell: float = 0.001
    stt_intraday_sell: float = 0.00025
    stamp_delivery_buy: float = 0.00015
    stamp_intraday_buy: float = 0.00003
    exchange_txn_rate: float = 0.0000322
    gst_rate: float = 0.18
    sebi_rate: float = 0.000001
    slippage_bps_by_bucket: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self.slippage_bps_by_bucket is None:
            object.__setattr__(
                self,
                "slippage_bps_by_bucket",
                {
                    "large": 3.0,
                    "liquid": 5.0,
                    "medium": 8.0,
                    "small": 12.0,
                    "illiquid": 20.0,
                    "unknown": 8.0,
                },
            )

    def one_side(
        self,
        notional: float,
        *,
        side: Side,
        product: ProductType = "delivery",
        liquidity_bucket: str = "unknown",
        include_slippage: bool = True,
    ) -> dict[str, float]:
        if notional < 0:
            raise ValueError("notional must be non-negative")
        brokerage = notional * self.brokerage_rate
        if self.brokerage_cap_per_order is not None:
            brokerage = min(brokerage, self.brokerage_cap_per_order)
        stt = notional * _stt_rate(self, side, product)
        stamp = notional * _stamp_rate(self, side, product)
        exchange_txn = notional * self.exchange_txn_rate
        sebi = notional * self.sebi_rate
        gst = (brokerage + exchange_txn) * self.gst_rate
        slip_bps = self.slippage_bps(liquidity_bucket) if include_slippage else 0.0
        slippage = notional * slip_bps / 10000.0
        total = brokerage + stt + stamp + exchange_txn + sebi + gst + slippage
        return {
            "notional": float(notional),
            "brokerage": brokerage,
            "stt": stt,
            "stamp": stamp,
            "exchange_txn": exchange_txn,
            "sebi": sebi,
            "gst": gst,
            "slippage": slippage,
            "total": total,
            "total_bps": total / notional * 10000.0 if notional else 0.0,
        }

    def round_trip(
        self,
        entry_notional: float,
        exit_notional: float | None = None,
        *,
        product: ProductType = "delivery",
        liquidity_bucket: str = "unknown",
        include_slippage: bool = True,
    ) -> dict[str, float]:
        exit_value = entry_notional if exit_notional is None else exit_notional
        buy = self.one_side(
            entry_notional,
            side="buy",
            product=product,
            liquidity_bucket=liquidity_bucket,
            include_slippage=include_slippage,
        )
        sell = self.one_side(
            exit_value,
            side="sell",
            product=product,
            liquidity_bucket=liquidity_bucket,
            include_slippage=include_slippage,
        )
        total = buy["total"] + sell["total"]
        denominator = entry_notional if entry_notional else 1.0
        return {
            "product": product,
            "liquidity_bucket": liquidity_bucket,
            "entry_notional": float(entry_notional),
            "exit_notional": float(exit_value),
            "buy": buy,
            "sell": sell,
            "total": total,
            "total_bps": total / denominator * 10000.0,
        }

    def slippage_bps(self, liquidity_bucket: str) -> float:
        table = self.slippage_bps_by_bucket or {}
        return float(table.get(str(liquidity_bucket).lower(), table.get("unknown", 8.0)))


def calculate_round_trip_cost(
    notional: float,
    *,
    product: ProductType = "delivery",
    liquidity_bucket: str = "unknown",
    exit_notional: float | None = None,
    model: IndianEquityCostModel | None = None,
) -> dict[str, float]:
    cost_model = model or DEFAULT_COST_MODEL
    return cost_model.round_trip(notional, exit_notional, product=product, liquidity_bucket=liquidity_bucket)


def _stt_rate(model: IndianEquityCostModel, side: Side, product: ProductType) -> float:
    if side != "sell":
        return 0.0
    if product == "delivery":
        return model.stt_delivery_sell
    if product == "intraday":
        return model.stt_intraday_sell
    raise ValueError(f"unknown product: {product}")


def _stamp_rate(model: IndianEquityCostModel, side: Side, product: ProductType) -> float:
    if side != "buy":
        return 0.0
    if product == "delivery":
        return model.stamp_delivery_buy
    if product == "intraday":
        return model.stamp_intraday_buy
    raise ValueError(f"unknown product: {product}")


DEFAULT_COST_MODEL = IndianEquityCostModel()
DEFAULT_DELIVERY_ROUND_TRIP_BPS = DEFAULT_COST_MODEL.round_trip(100_000, product="delivery", liquidity_bucket="liquid")[
    "total_bps"
]
