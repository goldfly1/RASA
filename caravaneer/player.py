"""
Player state: ships, cargo, modules, reputation, and finances.

Tracks everything a player owns and their standing in the galaxy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .core import (
    Position, Faction, ShipTemplate, ModuleTemplate, ModuleSlot,
    TradeGood, TRADE_GOODS, SHIP_TEMPLATES, MODULE_TEMPLATES,
)


@dataclass
class CargoItem:
    """A stack of trade goods in a ship's cargo hold."""
    good_key: str
    quantity: int
    purchase_price: int = 0   # Per unit, for profit calculation

    @property
    def good(self) -> TradeGood:
        return TRADE_GOODS[self.good_key]

    @property
    def total_weight(self) -> float:
        return self.good.weight * self.quantity

    @property
    def total_value(self) -> int:
        return self.good.base_price * self.quantity


@dataclass
class ShipModule:
    """An installed module on a ship."""
    template_key: str
    slot: ModuleSlot

    @property
    def template(self) -> ModuleTemplate:
        return MODULE_TEMPLATES[self.template_key]


@dataclass
class Ship:
    """A player's ship — their primary game piece."""
    name: str
    template_key: str
    position: Position
    owner_id: str

    # Current state
    fuel: int = 0
    hull: int = 0
    shields: int = 0
    cargo_hold: list[CargoItem] = field(default_factory=list)
    modules: list[ShipModule] = field(default_factory=list)
    credits: int = 0

    # Derived from template + modules
    @property
    def template(self) -> ShipTemplate:
        return SHIP_TEMPLATES[self.template_key]

    @property
    def max_cargo(self) -> int:
        base = self.template.cargo_capacity
        for mod in self.modules:
            if mod.template.slot == ModuleSlot.CARGO:
                base += int(mod.template.effect_value)
        return base

    @property
    def max_fuel(self) -> int:
        base = self.template.fuel_capacity
        for mod in self.modules:
            if mod.template.slot == ModuleSlot.UTILITY and "fuel" in mod.template.name.lower():
                base += int(mod.template.effect_value)
        return base

    @property
    def max_hull(self) -> int:
        return self.template.hull_points

    @property
    def max_shields(self) -> int:
        base = self.template.shield_points
        for mod in self.modules:
            if mod.template.slot == ModuleSlot.SHIELD:
                base += int(mod.template.effect_value)
        return base

    @property
    def jump_range(self) -> int:
        base = self.template.jump_range
        for mod in self.modules:
            if mod.template.slot == ModuleSlot.ENGINE:
                base += int(mod.template.effect_value)
        return base

    @property
    def speed(self) -> int:
        return self.template.speed

    @property
    def attack_power(self) -> int:
        base = 5  # Base attack
        for mod in self.modules:
            if mod.template.slot == ModuleSlot.WEAPON:
                base += int(mod.template.effect_value)
        return base

    @property
    def sensor_range(self) -> int:
        base = 2  # Base sensor range
        for mod in self.modules:
            if mod.template.slot == ModuleSlot.SENSOR:
                base = max(base, int(mod.template.effect_value))
        return base

    @property
    def used_cargo(self) -> float:
        return sum(item.total_weight for item in self.cargo_hold)

    @property
    def free_cargo(self) -> float:
        return self.max_cargo - self.used_cargo

    def has_module(self, effect_name: str) -> bool:
        """Check if ship has a module with a given effect (by name substring)."""
        for mod in self.modules:
            if effect_name.lower() in mod.template.name.lower():
                return True
        return False

    def can_fit(self, good_key: str, quantity: int = 1) -> bool:
        good = TRADE_GOODS[good_key]
        return (good.weight * quantity) <= self.free_cargo

    def add_cargo(self, good_key: str, quantity: int, price: int = 0) -> bool:
        if not self.can_fit(good_key, quantity):
            return False
        # Merge with existing stack if same good and price
        for item in self.cargo_hold:
            if item.good_key == good_key and item.purchase_price == price:
                item.quantity += quantity
                return True
        self.cargo_hold.append(CargoItem(good_key, quantity, price))
        return True

    def remove_cargo(self, good_key: str, quantity: int) -> bool:
        for item in self.cargo_hold:
            if item.good_key == good_key:
                if item.quantity >= quantity:
                    item.quantity -= quantity
                    if item.quantity == 0:
                        self.cargo_hold.remove(item)
                    return True
        return False

    def take_damage(self, amount: int):
        """Apply damage: shields first, then hull."""
        if self.shields > 0:
            absorbed = min(self.shields, amount)
            self.shields -= absorbed
            amount -= absorbed
        self.hull -= amount

    def is_destroyed(self) -> bool:
        return self.hull <= 0

    def repair(self, amount: int):
        self.hull = min(self.max_hull, self.hull + amount)

    def recharge_shields(self, amount: int):
        self.shields = min(self.max_shields, self.shields + amount)

    def refuel(self, amount: int):
        self.fuel = min(self.max_fuel, self.fuel + amount)


@dataclass
class Player:
    """Full player state including reputation, discovered systems, and stats."""
    id: str
    name: str
    ships: list[Ship] = field(default_factory=list)
    reputation: dict[Faction, int] = field(default_factory=dict)
    discovered_systems: set[Position] = field(default_factory=set)
    systems_traded_in: set[Position] = field(default_factory=set)
    total_credits_earned: int = 0
    total_trades: int = 0
    pirates_defeated: int = 0
    discoveries_made: int = 0
    turns_played: int = 0

    @property
    def primary_ship(self) -> Optional[Ship]:
        return self.ships[0] if self.ships else None

    @property
    def net_worth(self) -> int:
        """Total value: credits + ship value + cargo value."""
        total = 0
        for ship in self.ships:
            total += ship.credits
            total += ship.template.base_cost // 2  # Depreciated ship value
            for item in ship.cargo_hold:
                total += item.total_value
            for mod in ship.modules:
                total += mod.template.cost // 2
        return total

    def get_reputation(self, faction: Faction) -> int:
        return self.reputation.get(faction, 0)

    def modify_reputation(self, faction: Faction, delta: int):
        current = self.reputation.get(faction, 0)
        self.reputation[faction] = max(-100, min(100, current + delta))


def create_starting_ship(
    template_key: str,
    position: Position,
    owner_id: str,
    name: str = "",
    starting_credits: int = 1000,
) -> Ship:
    """Create a new ship with full fuel, hull, and shields."""
    template = SHIP_TEMPLATES[template_key]
    ship = Ship(
        name=name or f"{template.name}-{owner_id[:4]}",
        template_key=template_key,
        position=position,
        owner_id=owner_id,
        fuel=template.fuel_capacity,
        hull=template.hull_points,
        shields=template.shield_points,
        credits=starting_credits,
    )
    return ship
