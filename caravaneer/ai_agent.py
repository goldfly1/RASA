"""
Deterministic AI agent system for NPC opponents.

No LLM required — all decisions made via game-state evaluation, pathfinding,
risk models, and personality-driven goal selection.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from .core import Faction, Position, TerrainType, TRADE_GOODS, HexDir, MODULE_TEMPLATES, ModuleSlot
from .map import GalaxyMap, SystemData, TerrainCell
from .player import Ship, Player
from .travel import TravelEngine, TravelOption
from .economy import Economy
from .combat import CombatEngine, CombatResult
from .tech_tree import TechTreeManager, TECH_TREES
from .rumors import Merchant


class AIPersonality:
    """NPC behavioral archetypes."""
    TRADER = "trader"         # Profit-maximizing, avoids risk
    EXPLORER = "explorer"     # Seeks discovery, accepts moderate risk
    PIRATE = "pirate"         # Preys on weak ships, attacks for cargo
    SMUGGLER = "smuggler"     # Runs illegal goods, high risk tolerance
    MERCENARY = "mercenary"   # Takes bounties, fights for highest bidder


@dataclass
class AIMemory:
    """What an NPC remembers about the galaxy."""
    price_memory: dict[Position, dict[str, int]] = field(default_factory=dict)  # system -> good -> price
    danger_memory: dict[Position, float] = field(default_factory=dict)         # system -> danger score
    profitable_routes: list[tuple[Position, Position, str, float]] = field(default_factory=list)
    player_encounters: dict[str, int] = field(default_factory=dict)  # player_id -> reputation estimate
    last_visited: set[Position] = field(default_factory=set)


@dataclass
class AIProfile:
    """Full AI configuration for an NPC."""
    personality: str
    risk_tolerance: float       # 0.0 = coward, 1.0 = reckless
    combat_threshold: float     # Will attack if (self.attack / enemy.shield+hull) > threshold
    fuel_threshold: float       # Min fuel % before refusing travel (0.2 = 20%)
    hull_threshold: float       # Min hull % before avoiding hazards (0.3 = 30%)
    exploration_weight: float   # How much discovery is valued
    trade_memory: AIMemory = field(default_factory=AIMemory)
    tech_goals: list[str] = field(default_factory=list)  # Tech keys they want to unlock
    target_player: Optional[str] = None  # For pirates/mercenaries


def create_profile(personality: str) -> AIProfile:
    """Create a personality-tuned AI profile."""
    profiles = {
        AIPersonality.TRADER: AIProfile(
            personality=AIPersonality.TRADER,
            risk_tolerance=0.3,
            combat_threshold=2.0,  # Never attacks unless overwhelming
            fuel_threshold=0.25,
            hull_threshold=0.4,
            exploration_weight=0.1,
        ),
        AIPersonality.EXPLORER: AIProfile(
            personality=AIPersonality.EXPLORER,
            risk_tolerance=0.5,
            combat_threshold=1.5,
            fuel_threshold=0.20,
            hull_threshold=0.25,
            exploration_weight=0.8,
        ),
        AIPersonality.PIRATE: AIProfile(
            personality=AIPersonality.PIRATE,
            risk_tolerance=0.7,
            combat_threshold=0.8,  # Attacks when they have advantage
            fuel_threshold=0.15,
            hull_threshold=0.2,
            exploration_weight=0.1,
        ),
        AIPersonality.SMUGGLER: AIProfile(
            personality=AIPersonality.SMUGGLER,
            risk_tolerance=0.6,
            combat_threshold=1.2,
            fuel_threshold=0.20,
            hull_threshold=0.3,
            exploration_weight=0.2,
        ),
        AIPersonality.MERCENARY: AIProfile(
            personality=AIPersonality.MERCENARY,
            risk_tolerance=0.5,
            combat_threshold=1.0,
            fuel_threshold=0.20,
            hull_threshold=0.3,
            exploration_weight=0.3,
        ),
    }
    return profiles.get(personality, profiles[AIPersonality.TRADER])


class AIAgent:
    """
    A deterministic AI agent that controls an NPC ship.
    Evaluates game state, makes decisions, executes actions.
    """

    def __init__(
        self,
        player_id: str,
        galaxy: GalaxyMap,
        travel_engine: TravelEngine,
        economy: Economy,
        combat_engine: CombatEngine,
        tech_manager: TechTreeManager,
        profile: AIProfile,
        rng: Optional[random.Random] = None,
    ):
        self.player_id = player_id
        self.galaxy = galaxy
        self.travel = travel_engine
        self.economy = economy
        self.combat = combat_engine
        self.tech = tech_manager
        self.profile = profile
        self.rng = rng or random.Random()

    # ── Decision Entry Point ─────────────────────────────────────────────

    def take_turn(self, ship: Ship, all_players: dict[str, Player]) -> list[str]:
        """Execute a full AI turn. Returns log messages."""
        log: list[str] = []
        player = all_players.get(self.player_id)
        if not player or not player.primary_ship:
            return log
        ship = player.primary_ship

        # 1. Update memory
        self._update_memory(ship, player)

        # 2. Evaluate threats
        threat_action = self._evaluate_threats(ship, all_players, log)
        if threat_action == "fled":
            return log

        # 3. Evaluate combat opportunities
        combat_action = self._evaluate_combat(ship, all_players, log)
        if combat_action == "attacked":
            return log

        # 4. Evaluate trade/exploration
        self._evaluate_opportunity(ship, player, log)

        # 5. Tech unlocks
        self._evaluate_tech(ship, player, log)

        return log

    # ── Memory ────────────────────────────────────────────────────────────

    def _update_memory(self, ship: Ship, player: Player):
        """Record current system prices and state."""
        cell = self.galaxy.get_cell(ship.position)
        if cell and cell.system:
            # Record prices
            summary = self.economy.market_summary(cell.system)
            self.profile.trade_memory.price_memory[ship.position] = {
                k: v["price"] for k, v in summary.items()
            }
            # Clear old danger if we've visited safely
            if ship.position in self.profile.trade_memory.danger_memory:
                self.profile.trade_memory.danger_memory[ship.position] *= 0.8
        self.profile.trade_memory.last_visited.add(ship.position)

    # ── Threats ───────────────────────────────────────────────────────────

    def _evaluate_threats(self, ship: Ship, all_players: dict[str, Player], log: list[str]) -> Optional[str]:
        """Check if we need to flee from immediate danger."""
        # Low fuel = find nearest system NOW
        fuel_ratio = ship.fuel / ship.max_fuel if ship.max_fuel > 0 else 0
        if fuel_ratio < self.profile.fuel_threshold:
            dest = self._find_nearest_safe_haven(ship)
            if dest and dest != ship.position:
                result = self.travel.travel(ship, dest)
                if result.success:
                    log.append(f"NPC {ship.name} fled to {dest} (low fuel).")
                    return "fled"

        # Low hull = avoid hazards
        hull_ratio = ship.hull / ship.max_hull if ship.max_hull > 0 else 0
        if hull_ratio < self.profile.hull_threshold:
            # Move to nearest safe system
            dest = self._find_nearest_safe_haven(ship)
            if dest and dest != ship.position:
                result = self.travel.travel(ship, dest)
                if result.success:
                    log.append(f"NPC {ship.name} retreated to {dest} (low hull).")
                    return "fled"

        return None

    # ── Combat ────────────────────────────────────────────────────────────

    def _evaluate_combat(self, ship: Ship, all_players: dict[str, Player], log: list[str]) -> Optional[str]:
        """Decide whether to attack another ship in the same hex."""
        # Find targets in same position
        targets = []
        for pid, p in all_players.items():
            if pid == self.player_id:
                continue
            ps = p.primary_ship
            if ps and ps.position == ship.position:
                targets.append(ps)

        if not targets:
            return None

        # Pick weakest target
        targets.sort(key=lambda t: t.hull + t.shields)
        target = targets[0]

        # Combat evaluation
        enemy_defense = target.hull + target.shields
        if enemy_defense <= 0:
            enemy_defense = 1
        power_ratio = ship.attack_power / enemy_defense

        # Pirates attack when they have advantage
        if self.profile.personality == AIPersonality.PIRATE and power_ratio >= self.profile.combat_threshold:
            result = self.combat.resolve(ship, target, attacker_wants_flee=False, defender_wants_flee=True)
            log.append(f"NPC {ship.name} attacked {target.name}!")
            for msg in result.log:
                log.append(f"  {msg}")
            if result.defender_destroyed:
                log.append(f"  {target.name} was destroyed by {ship.name}!")
            return "attacked"

        # Mercenaries attack bounty targets
        if self.profile.personality == AIPersonality.MERCENARY and self.profile.target_player:
            if target.owner_id == self.profile.target_player and power_ratio >= 0.8:
                result = self.combat.resolve(ship, target, attacker_wants_flee=False, defender_wants_flee=True)
                log.append(f"Mercenary {ship.name} attacked bounty target {target.name}!")
                for msg in result.log:
                    log.append(f"  {msg}")
                return "attacked"

        return None

    # ── Trade / Exploration ───────────────────────────────────────────────

    def _evaluate_opportunity(self, ship: Ship, player: Player, log: list[str]):
        """Decide where to travel and what to trade."""
        options = self.travel.get_valid_destinations(ship)
        if not options:
            return

        # Score each destination
        scored = []
        for opt in options:
            score = self._score_destination(ship, player, opt)
            scored.append((score, opt))

        scored.sort(reverse=True, key=lambda x: x[0])
        best = scored[0][1]

        # Travel
        result = self.travel.travel(ship, best.destination)
        if result.success:
            log.append(f"NPC {ship.name} traveled to {best.system_name or best.terrain.name}.")
            for evt in result.events:
                log.append(f"  -> {evt.description}")

            # Trade at destination
            self._execute_trade(ship, player, log)

    def _score_destination(self, ship: Ship, player: Player, opt: TravelOption) -> float:
        """Score a travel destination. Higher = better."""
        score = 0.0
        cell = self.galaxy.get_cell(opt.destination)

        # Exploration bonus for undiscovered systems
        if opt.destination not in self.profile.trade_memory.last_visited:
            score += self.profile.exploration_weight * 50

        # Trade profit potential
        if cell and cell.system:
            prices = self.profile.trade_memory.price_memory.get(opt.destination, {})
            cargo = ship.cargo_hold
            for item in cargo:
                old_price = item.purchase_price
                new_price = prices.get(item.good_key, old_price)
                profit_potential = (new_price - old_price) * item.quantity
                score += profit_potential * 0.5

            # Buy cheap goods here
            summary = self.economy.market_summary(cell.system)
            for gk, info in summary.items():
                if info["trend"] == "down" and not info["illegal"]:
                    score += 20  # Buying opportunity
                elif info["trend"] == "up" and not info["illegal"]:
                    # Good place to sell... but we need cargo
                    pass

        # Risk penalties
        if opt.hazards:
            hazard_penalty = sum(20 for h in opt.hazards if "Asteroid" in h or "Black" in h)
            hazard_penalty += sum(10 for h in opt.hazards if "Pirate" in h)
            score -= hazard_penalty * (1.0 - self.profile.risk_tolerance)

        # Fuel cost
        score -= opt.fuel_cost * 2

        # Distance penalty (prefer closer)
        score -= opt.distance * 3

        return score

    def _execute_trade(self, ship: Ship, player: Player, log: list[str]):
        """Buy/sell at current system."""
        cell = self.galaxy.get_cell(ship.position)
        if not cell or not cell.system:
            return

        summary = self.economy.market_summary(cell.system)

        # Sell expensive goods
        for item in list(ship.cargo_hold):
            info = summary.get(item.good_key)
            if info and info["trend"] == "up" and not info["illegal"]:
                qty = min(item.quantity, 3)
                result = self.economy.sell(ship, cell.system, item.good_key, qty)
                if result.success:
                    log.append(f"  NPC {ship.name} sold {qty}x {item.good.name}.")

        # Buy cheap goods
        cheap = [(k, v) for k, v in summary.items()
                 if v["trend"] == "down" and not v["illegal"]]
        if cheap and ship.credits > 200:
            g = self.rng.choice(cheap)
            good = TRADE_GOODS[g[0]]
            qty = min(5, int(ship.free_cargo / good.weight)) if ship.free_cargo > 0 else 0
            if qty > 0:
                result = self.economy.buy(ship, cell.system, g[0], qty)
                if result.success:
                    log.append(f"  NPC {ship.name} bought {qty}x {good.name}.")

        # Smugglers buy illegal goods
        if self.profile.personality == AIPersonality.SMUGGLER:
            illegal = [(k, v) for k, v in summary.items() if v["illegal"]]
            if illegal and ship.credits > 500:
                g = self.rng.choice(illegal)
                good = TRADE_GOODS[g[0]]
                qty = min(3, int(ship.free_cargo / good.weight)) if ship.free_cargo > 0 else 0
                if qty > 0:
                    result = self.economy.buy(ship, cell.system, g[0], qty)
                    if result.success:
                        log.append(f"  NPC {ship.name} smuggled {qty}x {good.name}!")

    # ── Tech ──────────────────────────────────────────────────────────────

    def _evaluate_tech(self, ship: Ship, player: Player, log: list[str]):
        """Attempt to unlock technologies."""
        available = self.tech.available_techs(player)
        if not available:
            return
        # Prioritize by personality
        if self.profile.personality == AIPersonality.PIRATE:
            available.sort(key=lambda t: t.branch.name in ("WEAPONS", "ESPIONAGE"), reverse=True)
        elif self.profile.personality == AIPersonality.TRADER:
            available.sort(key=lambda t: t.branch.name in ("INDUSTRY", "PROPULSION"), reverse=True)
        elif self.profile.personality == AIPersonality.EXPLORER:
            available.sort(key=lambda t: t.branch.name in ("SENSORS", "PROPULSION"), reverse=True)

        # Try to buy the best affordable tech
        for tech in available[:3]:
            ok, msg = self.tech.unlock(player, tech.key)
            if ok:
                log.append(f"  NPC {ship.name} unlocked {tech.name}!")
                break

    # ── Helpers ───────────────────────────────────────────────────────────

    def _find_nearest_safe_haven(self, ship: Ship) -> Optional[Position]:
        """Find nearest safe star system."""
        options = self.travel.get_valid_destinations(ship)
        safe = [o for o in options if not o.hazards]
        if safe:
            safe.sort(key=lambda o: o.distance)
            return safe[0].destination
        if options:
            options.sort(key=lambda o: o.distance)
            return options[0].destination
        return None
