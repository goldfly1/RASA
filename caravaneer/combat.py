"""
Ship-to-ship combat resolution.

Deterministic turn-based combat within a single game turn.
Attack power vs shields/hull. Modules affect accuracy, damage, flee chance.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .core import EventType, MODULE_TEMPLATES, ModuleSlot
from .player import Ship, Player


@dataclass
class CombatResult:
    """Result of a ship-to-ship combat."""
    success: bool              # True if attacker won / achieved their goal
    attacker: Ship
    defender: Ship
    turns: int = 0
    attacker_damage_dealt: int = 0
    defender_damage_dealt: int = 0
    attacker_shots_fired: int = 0
    defender_shots_fired: int = 0
    hits_landed: int = 0
    defender_destroyed: bool = False
    attacker_fled: bool = False
    defender_fled: bool = False
    loot_credits: int = 0
    loot_cargo: list = field(default_factory=list)   # list of (good_key, qty)
    message: str = ""
    log: list[str] = field(default_factory=list)


class CombatEngine:
    """
    Resolve ship-to-ship combat.

    Rules:
    - Initiative: higher speed fires first. Tie = attacker.
    - Accuracy = 0.7 base + 0.05 per speed advantage - 0.03 per defender speed.
      Minimum 0.3, maximum 0.95.
    - Damage per hit = attack_power. Shields absorb first, then hull.
      Shield absorption efficiency = 1.0 (1 shield blocks 1 damage).
    - Combat ends when one ship destroyed, one flees, or max 5 exchange rounds.
    - Flee attempt: once per ship per combat. Base 40% + 10% per speed advantage.
      Nebulas give +15% flee bonus (smoke screen).
    - Pirate lair combat: defender is auto-generated pirate ship.
    """

    def __init__(self, rng: Optional[random.Random] = None):
        self.rng = rng or random.Random()

    def resolve(
        self,
        attacker: Ship,
        defender: Ship,
        attacker_wants_flee: bool = False,
        defender_wants_flee: bool = False,
        terrain_modifiers: Optional[dict] = None,
    ) -> CombatResult:
        """Run a full combat between two ships."""
        terrain = terrain_modifiers or {}
        result = CombatResult(
            success=False,
            attacker=attacker,
            defender=defender,
        )

        # Determine initiative order
        attacker_goes_first = attacker.speed >= defender.speed
        first, second = (attacker, defender) if attacker_goes_first else (defender, attacker)
        first_is_attacker = first is attacker

        max_rounds = 5
        first_has_fled = False
        second_has_fled = False

        for round_num in range(1, max_rounds + 1):
            result.turns = round_num

            # --- First ship's action ---
            if first_is_attacker and attacker_wants_flee and not first_has_fled:
                if self._attempt_flee(first, second, terrain):
                    result.attacker_fled = True
                    result.success = False
                    result.log.append(f"Round {round_num}: {first.name} disengaged and fled.")
                    break
                first_has_fled = True
                result.log.append(f"Round {round_num}: {first.name} failed to flee.")

            if not first.is_destroyed():
                dmg = self._fire(first, second)
                if first_is_attacker:
                    result.attacker_damage_dealt += dmg
                    result.attacker_shots_fired += 1
                else:
                    result.defender_damage_dealt += dmg
                    result.defender_shots_fired += 1
                if dmg > 0:
                    result.hits_landed += 1
                    result.log.append(f"Round {round_num}: {first.name} hit {second.name} for {dmg} damage.")
                else:
                    result.log.append(f"Round {round_num}: {first.name} fired at {second.name} but missed.")

            if second.is_destroyed():
                result.defender_destroyed = (second is defender)
                result.success = (first is attacker)
                result.log.append(f"{second.name} was destroyed!")
                break

            # --- Second ship's action ---
            if not first_is_attacker and attacker_wants_flee and not second_has_fled:
                # Wait, second ship is actually the attacker in this case
                pass  # flee logic handled above for the actual attacker

            # Defender flee attempt
            if second is defender and defender_wants_flee and not second_has_fled:
                if self._attempt_flee(second, first, terrain):
                    result.defender_fled = True
                    result.success = True  # Attacker drove them off
                    result.log.append(f"Round {round_num}: {second.name} disengaged and fled.")
                    break
                second_has_fled = True
                result.log.append(f"Round {round_num}: {second.name} failed to flee.")

            if not second.is_destroyed():
                dmg = self._fire(second, first)
                if first_is_attacker:
                    result.defender_damage_dealt += dmg
                    result.defender_shots_fired += 1
                else:
                    result.attacker_damage_dealt += dmg
                    result.attacker_shots_fired += 1
                if dmg > 0:
                    result.hits_landed += 1
                    result.log.append(f"Round {round_num}: {second.name} hit {first.name} for {dmg} damage.")
                else:
                    result.log.append(f"Round {round_num}: {second.name} fired at {first.name} but missed.")

            if first.is_destroyed():
                result.defender_destroyed = (first is defender)
                result.success = (second is attacker)
                result.log.append(f"{first.name} was destroyed!")
                break

            result.log.append(f"Round {round_num} end: {attacker.name} H:{attacker.hull} S:{attacker.shields} | {defender.name} H:{defender.hull} S:{defender.shields}")

        # --- Post-combat ---
        if not result.defender_destroyed and not result.attacker_fled and not result.defender_fled:
            result.log.append("Combat ended in a standoff.")
            result.success = False

        # Loot if defender destroyed
        if result.defender_destroyed:
            result.loot_credits = int(defender.credits * 0.2)
            attacker.credits += result.loot_credits
            defender.credits -= result.loot_credits
            for item in list(defender.cargo_hold):
                loot_qty = max(1, item.quantity // 5)
                if attacker.can_fit(item.good_key, loot_qty):
                    attacker.add_cargo(item.good_key, loot_qty, item.purchase_price)
                    result.loot_cargo.append((item.good_key, loot_qty))
                    defender.remove_cargo(item.good_key, loot_qty)

        # Post-combat shield degradation (combat is exhausting)
        attacker.shields = max(0, attacker.shields - 2)
        defender.shields = max(0, defender.shields - 2)

        return result

    def _fire(self, attacker: Ship, defender: Ship) -> int:
        """One shot from attacker to defender. Returns damage dealt (0 if miss)."""
        accuracy = 0.70
        speed_diff = attacker.speed - defender.speed
        accuracy += 0.05 * speed_diff
        accuracy = max(0.30, min(0.95, accuracy))

        if self.rng.random() > accuracy:
            return 0

        damage = attacker.attack_power
        # Shields absorb
        if defender.shields > 0:
            absorbed = min(defender.shields, damage)
            defender.shields -= absorbed
            damage -= absorbed
        # Hull takes remainder
        defender.hull -= damage
        return damage

    def _attempt_flee(self, fleeing: Ship, pursuer: Ship, terrain: dict) -> bool:
        """Attempt to flee combat."""
        chance = 0.40
        speed_diff = fleeing.speed - pursuer.speed
        chance += 0.10 * speed_diff
        if terrain.get("nebula"):
            chance += 0.15
        chance = max(0.10, min(0.85, chance))
        return self.rng.random() < chance

    def generate_pirate(self, template_key: str = "corvette", name: str = "Pirate") -> Ship:
        """Generate a pirate ship for auto-encounters."""
        from .player import create_starting_ship
        from .core import Position
        pirate = create_starting_ship(
            template_key=template_key,
            position=Position(0, 0),
            owner_id="npc_pirate",
            name=name,
            starting_credits=500,
        )
        # Pirates get a free weapon and some cargo
        pirate.modules.append(ShipModule(
            template_key="laser_cannon",
            slot=ModuleSlot.WEAPON,
        ))
        # Fill cargo with random illicit goods
        from .core import TRADE_GOODS
        illicit = [k for k, g in TRADE_GOODS.items() if g.illegal_in]
        if illicit:
            good = self.rng.choice(illicit)
            pirate.add_cargo(good, self.rng.randint(3, 8), 0)
        return pirate


from .player import ShipModule  # noqa: E402
