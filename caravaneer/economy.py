"""
Economy and trade system.

Handles buying, selling, price calculation, supply/demand,
and market events across the galaxy.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .core import (
    Position, Faction, SystemClass, TradeGood, TRADE_GOODS,
    EventType, GoodCategory,
)
from .map import GalaxyMap, SystemData
from .player import Ship, CargoItem, Player


@dataclass
class TradeResult:
    """Result of a buy or sell transaction."""
    success: bool
    action: str           # "buy" or "sell"
    good_key: str
    quantity: int
    unit_price: int
    total_cost: int
    message: str = ""


class Economy:
    """
    Manages the galactic economy: prices, supply/demand, and trade.
    """

    def __init__(
        self,
        galaxy: GalaxyMap,
        rng: Optional[random.Random] = None,
    ):
        self.galaxy = galaxy
        self.rng = rng or random.Random()
        self.active_events: dict[Position, list[MarketEvent]] = {}

    # ── Price Calculation ──────────────────────────────────────────────────

    def get_price(self, good_key: str, system: SystemData) -> int:
        """Calculate the current price of a good at a system."""
        good = TRADE_GOODS[good_key]
        base = good.base_price

        class_mod = self._class_price_modifier(system.system_class, good)
        trade_mod = system.price_modifiers.get(good_key, 1.0)

        event_mod = 1.0
        for event in self.active_events.get(system.position, []):
            if event.good_key in (good_key, "ALL"):
                event_mod *= event.price_multiplier

        price = int(base * class_mod * trade_mod * event_mod)
        return max(1, price)

    def _class_price_modifier(self, sys_class: SystemClass, good: TradeGood) -> float:
        """Price modifier based on system class and good category."""
        cat_name = good.category.name  # "RAW", "FOOD", etc.

        modifiers: dict[SystemClass, dict[str, float]] = {
            SystemClass.CAPITAL: {},
            SystemClass.MINING: {
                "RAW": 0.6, "FOOD": 1.3, "MANUFACTURED": 1.4,
                "LUXURY": 1.5, "TECH": 1.5, "CONTRABAND": 2.0,
            },
            SystemClass.AGRICULTURAL: {
                "RAW": 1.2, "FOOD": 0.5, "MANUFACTURED": 1.3,
                "LUXURY": 1.4, "TECH": 1.4, "CONTRABAND": 2.0,
            },
            SystemClass.INDUSTRIAL: {
                "RAW": 1.3, "FOOD": 1.2, "MANUFACTURED": 0.7,
                "LUXURY": 1.2, "TECH": 1.1, "CONTRABAND": 1.8,
            },
            SystemClass.RESEARCH: {
                "RAW": 1.2, "FOOD": 1.2, "MANUFACTURED": 1.1,
                "LUXURY": 1.3, "TECH": 0.6, "CONTRABAND": 1.5,
            },
            SystemClass.RUINS: {
                "RAW": 1.5, "FOOD": 1.5, "MANUFACTURED": 1.5,
                "LUXURY": 0.8, "TECH": 0.7, "CONTRABAND": 1.2,
            },
        }
        cat_mods = modifiers.get(sys_class, {})
        return cat_mods.get(cat_name, 1.0)

    # ── Trade Actions ──────────────────────────────────────────────────────

    def buy(
        self, ship: Ship, system: SystemData, good_key: str, quantity: int
    ) -> TradeResult:
        """Buy goods from a system."""
        good = TRADE_GOODS[good_key]
        price = self.get_price(good_key, system)

        if system.faction in good.illegal_in:
            return TradeResult(False, "buy", good_key, quantity, price, 0,
                               f"{good.name} is illegal in {system.faction.name} space!")

        if not ship.can_fit(good_key, quantity):
            return TradeResult(False, "buy", good_key, quantity, price, 0,
                               "Not enough cargo space.")

        total = price * quantity
        if ship.credits < total:
            return TradeResult(False, "buy", good_key, quantity, price, total,
                               f"Need {total} credits, have {ship.credits}.")

        ship.credits -= total
        ship.add_cargo(good_key, quantity, price)

        return TradeResult(True, "buy", good_key, quantity, price, total,
                           f"Bought {quantity}x {good.name} @ {price}cr each.")

    def sell(
        self, ship: Ship, system: SystemData, good_key: str, quantity: int
    ) -> TradeResult:
        """Sell goods to a system."""
        good = TRADE_GOODS[good_key]
        price = self.get_price(good_key, system)

        if system.faction in good.illegal_in:
            return TradeResult(False, "sell", good_key, quantity, price, 0,
                               f"{good.name} is illegal in {system.faction.name} space!")

        stack = None
        for item in ship.cargo_hold:
            if item.good_key == good_key:
                stack = item
                break

        if not stack or stack.quantity < quantity:
            return TradeResult(False, "sell", good_key, quantity, price, 0,
                               f"Don't have {quantity}x {good.name} in cargo.")

        total = price * quantity
        ship.credits += total
        ship.remove_cargo(good_key, quantity)

        profit = (price - stack.purchase_price) * quantity

        return TradeResult(True, "sell", good_key, quantity, price, total,
                           f"Sold {quantity}x {good.name} @ {price}cr each "
                           f"(profit: {profit}cr).")

    # ── Market Events ──────────────────────────────────────────────────────

    def generate_market_event(self) -> Optional[MarketEvent]:
        """Randomly generate a galaxy-wide market event."""
        if self.rng.random() > 0.2:
            return None

        event_type = self.rng.choice([
            EventType.MARKET_BOOM, EventType.MARKET_CRASH,
            EventType.GOLD_RUSH, EventType.PLAGUE,
            EventType.TRADE_CONVOY,
        ])

        good_key = self.rng.choice(list(TRADE_GOODS.keys()))
        multiplier = 1.0

        if event_type == EventType.MARKET_BOOM:
            multiplier = self.rng.uniform(1.5, 2.5)
        elif event_type == EventType.MARKET_CRASH:
            multiplier = self.rng.uniform(0.3, 0.6)
        elif event_type == EventType.GOLD_RUSH:
            multiplier = self.rng.uniform(2.0, 3.0)
        elif event_type == EventType.PLAGUE:
            multiplier = self.rng.uniform(0.2, 0.4)
        elif event_type == EventType.TRADE_CONVOY:
            multiplier = self.rng.uniform(0.5, 0.8)

        all_systems = [p for p, c in self.galaxy.cells.items() if c.system]
        affected = self.rng.sample(
            all_systems,
            min(self.rng.randint(2, 6), len(all_systems))
        )

        event = MarketEvent(
            event_type=event_type,
            good_key=good_key,
            price_multiplier=multiplier,
            affected_systems=affected,
            turns_remaining=self.rng.randint(2, 5),
        )

        for pos in affected:
            if pos not in self.active_events:
                self.active_events[pos] = []
            self.active_events[pos].append(event)

        return event

    def tick_events(self):
        """Decrement event durations and remove expired ones."""
        expired = []
        for pos, events in self.active_events.items():
            for event in events:
                event.turns_remaining -= 1
                if event.turns_remaining <= 0:
                    expired.append((pos, event))

        for pos, event in expired:
            if pos in self.active_events:
                self.active_events[pos].remove(event)
                if not self.active_events[pos]:
                    del self.active_events[pos]

    def market_summary(self, system: SystemData) -> dict[str, dict]:
        """Get a summary of all goods and prices at a system."""
        summary = {}
        for key, good in TRADE_GOODS.items():
            price = self.get_price(key, system)
            illegal = system.faction in good.illegal_in
            summary[key] = {
                "name": good.name,
                "price": price,
                "base_price": good.base_price,
                "category": good.category.name,
                "illegal": illegal,
                "trend": "up" if price > good.base_price else "down",
            }
        return summary


@dataclass
class MarketEvent:
    """A temporary market condition affecting prices."""
    event_type: EventType
    good_key: str
    price_multiplier: float
    affected_systems: list[Position]
    turns_remaining: int
