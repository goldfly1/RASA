"""
Game loop and turn manager — Caravaneer to the Stars.

Orchestrates the full game lifecycle: setup, turns, combat, AI opponents,
tech progression, rumors, merchant interaction, and victory checking.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional, Callable

from .core import (
    Position, TerrainType, Faction, SystemClass,
    EventType, VictoryType, DiscoveryType,
    SHIP_TEMPLATES, MODULE_TEMPLATES, TRADE_GOODS,
)
from .map import GalaxyMap, SystemData, generate_galaxy, generate_player_starts
from .player import Ship, Player, create_starting_ship
from .travel import TravelEngine, TravelResult, TravelOption
from .economy import Economy, TradeResult, MarketEvent
from .combat import CombatEngine, CombatResult
from .ai_agent import AIAgent, AIProfile, create_profile, AIPersonality
from .tech_tree import TechTreeManager, TechProgress, TECH_TREES, Technology
from .rumors import RumorEngine, Rumor, Merchant, generate_merchant


@dataclass
class TurnLog:
    """Record of everything that happened in a turn."""
    turn_number: int
    player_id: str
    actions: list[str] = field(default_factory=list)
    travel_results: list[TravelResult] = field(default_factory=list)
    trade_results: list[TradeResult] = field(default_factory=list)
    combat_results: list[CombatResult] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    discoveries: list[str] = field(default_factory=list)
    rumors: list[str] = field(default_factory=list)


@dataclass
class GameConfig:
    """Configuration for a new game."""
    galaxy_radius: int = 20
    system_density: float = 0.15
    num_players: int = 2
    num_factions: int = 4
    num_wormholes: int = 3
    npc_count: int = 3
    seed: Optional[int] = None
    victory_type: VictoryType = VictoryType.OPEN
    victory_target: int = 100_000
    starting_credits: int = 1000
    enable_npc_traders: bool = True
    enable_random_events: bool = True
    enable_combat: bool = True
    enable_tech_trees: bool = True
    enable_rumors: bool = True


class Game:
    """
    Main game controller. Manages the full game lifecycle:
    setup, turn processing, combat, AI, tech, rumors, and victory checking.
    """

    def __init__(self, config: GameConfig):
        self.config = config
        self.rng = random.Random(config.seed)

        # Generate galaxy
        self.galaxy = generate_galaxy(
            radius=config.galaxy_radius,
            system_density=config.system_density,
            num_wormholes=config.num_wormholes,
            num_factions=config.num_factions,
            seed=config.seed,
        )

        # Generate player starts
        self.player_starts = generate_player_starts(
            self.galaxy, config.num_players, rng=self.rng
        )

        # Initialize subsystems
        self.travel_engine = TravelEngine(self.galaxy, rng=self.rng)
        self.economy = Economy(self.galaxy, rng=self.rng)
        self.combat_engine = CombatEngine(rng=self.rng)
        self.tech_manager = TechTreeManager()
        self.rumor_engine = RumorEngine(rng=self.rng)

        # Game state
        self.players: dict[str, Player] = {}
        self.turn_number: int = 0
        self.turn_logs: list[TurnLog] = []
        self.game_over: bool = False
        self.winner: Optional[Player] = None
        self.victory_reason: str = ""

        # NPC ships (legacy list)
        self.npc_ships: list[Ship] = []
        # AI agents keyed by player_id (NPCs are also "players" in the dict)
        self.ai_agents: dict[str, AIAgent] = {}

        # Merchants per system
        self.merchants: dict[Position, Merchant] = {}

        # Generate merchants for all star systems
        for pos, cell in self.galaxy.cells.items():
            if cell.system:
                self.merchants[pos] = generate_merchant(cell.system, rng=self.rng)

    # ── Setup ──────────────────────────────────────────────────────────────

    def add_player(self, player_id: str, name: str, ship_template: str = "freighter") -> Player:
        """Add a human player to the game."""
        if len(self.players) >= len(self.player_starts):
            raise ValueError("No more starting positions available.")

        starts = self.player_starts[len(self.players)]
        primary_pos = starts[0]

        ship = create_starting_ship(
            template_key=ship_template,
            position=primary_pos,
            owner_id=player_id,
            name=f"{name}'s {SHIP_TEMPLATES[ship_template].name}",
            starting_credits=self.config.starting_credits,
        )

        player = Player(id=player_id, name=name, ships=[ship])

        # Initialize reputation
        for faction in Faction:
            player.reputation[faction] = 0

        # Reveal starting area
        self.galaxy.reveal(primary_pos, player_id, ship.sensor_range)
        for extra_pos in starts[1:]:
            self.galaxy.reveal(extra_pos, player_id, 1)

        self.players[player_id] = player
        return player

    def add_npc_trader(self, name: str = "", personality: str = "trader", ship_template: Optional[str] = None) -> Player:
        """Add an AI-controlled NPC opponent to the game."""
        if not self.config.enable_npc_traders:
            raise ValueError("NPC traders are disabled.")

        occupied = {p.primary_ship.position for p in self.players.values() if p.primary_ship}
        candidates = [
            p for p, c in self.galaxy.cells.items()
            if c.terrain == TerrainType.STAR_SYSTEM
            and p not in occupied
            and all(p.distance_to(op) > 5 for op in occupied)
        ]
        if not candidates:
            candidates = list(occupied)

        pos = self.rng.choice(candidates)
        template = ship_template or self.rng.choice(["freighter", "corvette", "explorer"])
        npc_id = f"npc_{len(self.npc_ships)}"
        ship = create_starting_ship(
            template_key=template,
            position=pos,
            owner_id=npc_id,
            name=name or f"NPC {personality.title()} {len(self.npc_ships) + 1}",
            starting_credits=self.rng.randint(2000, 10000),
        )

        # Give NPCs a starting weapon
        from .player import ShipModule
        from .core import ModuleSlot
        ship.modules.append(ShipModule(template_key="laser_cannon", slot=ModuleSlot.WEAPON))

        player = Player(id=npc_id, name=ship.name, ships=[ship])
        for faction in Faction:
            player.reputation[faction] = self.rng.randint(-10, 10)

        self.players[npc_id] = player
        self.npc_ships.append(ship)

        # Create AI agent
        profile = create_profile(personality)
        agent = AIAgent(
            player_id=npc_id,
            galaxy=self.galaxy,
            travel_engine=self.travel_engine,
            economy=self.economy,
            combat_engine=self.combat_engine,
            tech_manager=self.tech_manager,
            profile=profile,
            rng=self.rng,
        )
        self.ai_agents[npc_id] = agent

        # Reveal area for NPC
        self.galaxy.reveal(pos, npc_id, ship.sensor_range)

        return player

    # ── Turn Processing ────────────────────────────────────────────────────

    def process_turn(self, player_id: str) -> TurnLog:
        """
        Process a full turn for a player.
        """
        self.turn_number += 1
        self.rumor_engine.tick(self.turn_number)

        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            raise ValueError(f"Player {player_id} has no ship.")

        log = TurnLog(turn_number=self.turn_number, player_id=player_id)
        player.turns_played += 1

        # ── 1. Movement Phase ──────────────────────────────────────────
        log.actions.append(f"Movement phase: {ship.speed} jumps available.")

        # ── 2. Combat Phase (player encounters) ────────────────────────
        if self.config.enable_combat:
            # Pirate lair encounter
            cell = self.galaxy.get_cell(ship.position)
            if cell and cell.terrain == TerrainType.PIRATE_LAIR:
                pirate = self.combat_engine.generate_pirate()
                result = self.combat_engine.resolve(ship, pirate, attacker_wants_flee=False, defender_wants_flee=False)
                log.combat_results.append(result)
                for msg in result.log:
                    log.actions.append(f"Combat: {msg}")

            # Same-hex NPC encounters
            for npc_id, npc_player in self.players.items():
                if npc_id == player_id or npc_id not in self.ai_agents:
                    continue
                npc_ship = npc_player.primary_ship
                if npc_ship and npc_ship.position == ship.position:
                    # Check if hostile (pirates or bad rep)
                    agent = self.ai_agents[npc_id]
                    if agent.profile.personality == AIPersonality.PIRATE:
                        result = self.combat_engine.resolve(npc_ship, ship, attacker_wants_flee=False, defender_wants_flee=True)
                        log.combat_results.append(result)
                        for msg in result.log:
                            log.actions.append(f"Ambush: {msg}")

        # ── 3. Trade Phase ─────────────────────────────────────────────
        cell = self.galaxy.get_cell(ship.position)
        if cell and cell.system:
            log.actions.append(f"At {cell.system.name} ({cell.system.system_class.name}).")
            # Merchant greeting / rumor
            merchant = self.merchants.get(ship.position)
            if merchant and self.config.enable_rumors:
                log.rumors.append(merchant.greet(player))
                if merchant.should_share_rumor():
                    rumors = self.rumor_engine.get_rumors_at(ship.position, radius=3)
                    if rumors:
                        r = self.rng.choice(rumors)
                        log.rumors.append(f"Rumor: {r.text}")
                fake = merchant.fabricate_rumor(self.rumor_engine)
                if fake:
                    self.rumor_engine.add_rumor(fake)
                    log.rumors.append(f"Tip: {fake.text}")

        # ── 4. Random Events ───────────────────────────────────────────
        if self.config.enable_random_events:
            event = self.economy.generate_market_event()
            if event:
                log.events.append(
                    f"Market event: {event.event_type.name} affecting "
                    f"{TRADE_GOODS[event.good_key].name if event.good_key != 'ALL' else 'all goods'} "
                    f"({event.price_multiplier:.1f}x, {event.turns_remaining} turns)"
                )
                # Generate rumor from event
                if self.config.enable_rumors and event.affected_systems:
                    for sys_pos in event.affected_systems[:2]:
                        sys_cell = self.galaxy.get_cell(sys_pos)
                        if sys_cell and sys_cell.system:
                            rumor = self.rumor_engine.generate_market_rumor(
                                sys_cell.system, event.good_key,
                                "booming" if event.price_multiplier > 1.0 else "crashing"
                            )
                            self.rumor_engine.add_rumor(rumor)

        # ── 5. NPC AI Turn ─────────────────────────────────────────────
        if self.config.enable_npc_traders:
            for npc_id, agent in self.ai_agents.items():
                npc_player = self.players.get(npc_id)
                if not npc_player or not npc_player.primary_ship:
                    continue
                if npc_player.primary_ship.is_destroyed():
                    continue
                actions = agent.take_turn(npc_player.primary_ship, self.players)
                log.actions.extend(actions)

        # ── 6. End-of-turn Maintenance ─────────────────────────────────
        self._end_of_turn_maintenance(ship, log)

        # Tick economy events
        self.economy.tick_events()

        self.turn_logs.append(log)

        # Check victory
        self._check_victory(player)

        return log

    def _end_of_turn_maintenance(self, ship: Ship, log: TurnLog):
        """Handle repairs, shield recharge, etc."""
        # Tech passive bonuses
        progress = self.tech_manager.get_progress(ship.owner_id)
        extra_shield = progress.shield_regen

        # Repair drones
        if ship.has_module("repair"):
            ship.repair(5)
            log.actions.append("Repair drones restored 5 hull.")

        # Natural shield recharge + tech bonus
        recharge = 2 + extra_shield
        ship.recharge_shields(recharge)
        log.actions.append(f"Shields recharged by {recharge}.")

    # ── Player Actions ────────────────────────────────────────────────────

    def player_travel(self, player_id: str, destination: Position) -> TravelResult:
        """Move a player's ship to a destination."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return TravelResult(False, ship, [], message="No ship.")

        result = self.travel_engine.travel(ship, destination)

        if result.success:
            for event in result.events:
                if event.discovery and event.discovery != DiscoveryType.NOTHING:
                    player.discoveries_made += 1
            cell = self.galaxy.get_cell(destination)
            if cell and cell.system:
                player.discovered_systems.add(destination)

        return result

    def player_buy(self, player_id: str, good_key: str, quantity: int) -> TradeResult:
        """Buy goods at the player's current system."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return TradeResult(False, "buy", good_key, quantity, 0, 0, "No ship.")

        cell = self.galaxy.get_cell(ship.position)
        if not cell or not cell.system:
            return TradeResult(False, "buy", good_key, quantity, 0, 0,
                               "Not at a star system.")

        # Apply merchant personality price modifier
        merchant = self.merchants.get(ship.position)
        result = self.economy.buy(ship, cell.system, good_key, quantity)
        if result.success:
            player.systems_traded_in.add(ship.position)
            player.total_trades += 1
            # Reputation: trading with a faction gives small boost
            player.modify_reputation(cell.system.faction, 1)
            # Generate rumor
            if self.config.enable_rumors:
                rumor = self.rumor_engine.generate_player_rumor(player, "trading", cell.system)
                self.rumor_engine.add_rumor(rumor)
        return result

    def player_sell(self, player_id: str, good_key: str, quantity: int) -> TradeResult:
        """Sell goods at the player's current system."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return TradeResult(False, "sell", good_key, quantity, 0, 0, "No ship.")

        cell = self.galaxy.get_cell(ship.position)
        if not cell or not cell.system:
            return TradeResult(False, "sell", good_key, quantity, 0, 0,
                               "Not at a star system.")

        result = self.economy.sell(ship, cell.system, good_key, quantity)
        if result.success:
            player.systems_traded_in.add(ship.position)
            player.total_trades += 1
            player.total_credits_earned += result.total_cost
        return result

    def player_refuel(self, player_id: str) -> str:
        """Refuel at current system (costs credits)."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return "No ship."

        cell = self.galaxy.get_cell(ship.position)
        if not cell or not cell.system:
            return "Not at a star system."

        # Check if merchant likes us enough to sell fuel
        merchant = self.merchants.get(ship.position)
        rep = player.get_reputation(cell.system.faction)
        if rep < -50:
            return f"{cell.system.name} refuses service to enemies of {cell.system.faction.name}!"

        needed = ship.max_fuel - ship.fuel
        if needed <= 0:
            return "Fuel already full."

        cost = needed * 2
        if ship.credits < cost:
            return f"Need {cost} credits for fuel, have {ship.credits}."

        ship.credits -= cost
        ship.refuel(needed)
        return f"Refueled {needed} units for {cost} credits."

    def player_repair(self, player_id: str) -> str:
        """Repair at current system (costs credits)."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return "No ship."

        cell = self.galaxy.get_cell(ship.position)
        if not cell or not cell.system:
            return "Not at a star system."

        rep = player.get_reputation(cell.system.faction)
        if rep < -50:
            return f"{cell.system.name} refuses service to enemies of {cell.system.faction.name}!"

        needed = ship.max_hull - ship.hull
        if needed <= 0:
            return "Hull already at maximum."

        cost = needed * 5
        if ship.credits < cost:
            return f"Need {cost} credits for repairs, have {ship.credits}."

        ship.credits -= cost
        ship.repair(needed)
        return f"Repaired {needed} hull for {cost} credits."

    def player_attack(self, player_id: str, target_id: str) -> CombatResult:
        """Player initiates combat against another ship."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            raise ValueError("No ship.")

        target_player = self.players.get(target_id)
        if not target_player or not target_player.primary_ship:
            raise ValueError("Target not found.")

        target_ship = target_player.primary_ship
        if target_ship.position != ship.position:
            return CombatResult(
                success=False,
                attacker=ship,
                defender=target_ship,
                message="Target not in range.",
            )

        result = self.combat_engine.resolve(ship, target_ship, attacker_wants_flee=False, defender_wants_flee=True)

        # Reputation consequences
        if result.defender_destroyed:
            # Big reputation hit with target's faction
            cell = self.galaxy.get_cell(target_ship.position)
            if cell and cell.system:
                player.modify_reputation(cell.system.faction, -30)
            # Generate rumor
            if self.config.enable_rumors and cell and cell.system:
                rumor = self.rumor_engine.generate_player_rumor(
                    player, "attacking ships", cell.system,
                )
                self.rumor_engine.add_rumor(rumor)

        return result

    def player_unlock_tech(self, player_id: str, tech_key: str) -> tuple[bool, str]:
        """Attempt to unlock a technology."""
        player = self.players.get(player_id)
        if not player:
            return False, "Player not found."
        return self.tech_manager.unlock(player, tech_key)

    # ── Information ────────────────────────────────────────────────────────

    def get_travel_options(self, player_id: str) -> list[TravelOption]:
        """Get valid destinations for a player."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return []
        return self.travel_engine.get_valid_destinations(ship)

    def get_market(self, player_id: str) -> Optional[dict]:
        """Get market summary at player's current system."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return None
        cell = self.galaxy.get_cell(ship.position)
        if not cell or not cell.system:
            return None
        return self.economy.market_summary(cell.system)

    def get_merchant(self, player_id: str) -> Optional[Merchant]:
        """Get the merchant at the player's current system."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return None
        return self.merchants.get(ship.position)

    def get_rumors(self, player_id: str) -> list[Rumor]:
        """Get rumors near the player."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return []
        return self.rumor_engine.get_rumors_at(ship.position, radius=3)

    def get_tech_progress(self, player_id: str) -> TechProgress:
        """Get tech progress for a player."""
        return self.tech_manager.get_progress(player_id)

    def get_available_techs(self, player_id: str) -> list[Technology]:
        """Get unlockable techs for a player."""
        player = self.players.get(player_id)
        if not player:
            return []
        return self.tech_manager.available_techs(player)

    def get_visible_map(self, player_id: str) -> dict:
        """Get all visible cells for a player."""
        visible = self.galaxy.visible_cells(player_id)
        result = {}
        for pos, cell in visible.items():
            result[str(pos)] = {
                "terrain": cell.terrain.name,
                "system_name": cell.system.name if cell.system else None,
                "system_class": cell.system.system_class.name if cell.system else None,
                "faction": cell.system.faction.name if cell.system else None,
                "hazards": [],
            }
            if cell.terrain == TerrainType.ASTEROID_FIELD:
                result[str(pos)]["hazards"].append(f"Damage: {cell.hazard_damage}")
            elif cell.terrain == TerrainType.NEBULA:
                result[str(pos)]["hazards"].append("Drift risk")
            elif cell.terrain == TerrainType.PIRATE_LAIR:
                result[str(pos)]["hazards"].append("Pirates")
        return result

    def find_route(self, player_id: str, destination: Position) -> Optional[list[Position]]:
        """Find a multi-jump route to a destination."""
        player = self.players[player_id]
        ship = player.primary_ship
        if not ship:
            return None
        return self.travel_engine.find_path(ship, destination)

    # ── Victory ────────────────────────────────────────────────────────────

    def _check_victory(self, player: Player):
        """Check if a player has met victory conditions."""
        if self.config.victory_type == VictoryType.OPEN:
            return

        if self.config.victory_type == VictoryType.NET_WORTH:
            if player.net_worth >= self.config.victory_target:
                self.game_over = True
                self.winner = player
                self.victory_reason = f"Reached {self.config.victory_target} net worth!"

        elif self.config.victory_type == VictoryType.MOST_WEALTH:
            if self.turn_number >= 50:
                richest = max(self.players.values(), key=lambda p: p.net_worth)
                self.game_over = True
                self.winner = richest
                self.victory_reason = f"Richest player after {self.turn_number} turns!"

        elif self.config.victory_type == VictoryType.DISCOVERY:
            if player.discoveries_made >= self.config.victory_target:
                self.game_over = True
                self.winner = player
                self.victory_reason = f"Made {player.discoveries_made} discoveries!"

    def get_game_state(self, player_id: str) -> dict:
        """Get the full game state from a player's perspective."""
        player = self.players.get(player_id)
        if not player:
            return {"error": "Player not found"}

        ship = player.primary_ship
        cell = self.galaxy.get_cell(ship.position) if ship else None
        tech = self.tech_manager.get_progress(player_id)

        return {
            "turn": self.turn_number,
            "game_over": self.game_over,
            "winner": self.winner.name if self.winner else None,
            "victory_reason": self.victory_reason,
            "player": {
                "name": player.name,
                "net_worth": player.net_worth,
                "discoveries": player.discoveries_made,
                "trades": player.total_trades,
                "turns_played": player.turns_played,
                "reputation": {f.name: v for f, v in player.reputation.items()},
            },
            "ship": {
                "name": ship.name if ship else None,
                "position": str(ship.position) if ship else None,
                "fuel": f"{ship.fuel}/{ship.max_fuel}" if ship else None,
                "hull": f"{ship.hull}/{ship.max_hull}" if ship else None,
                "shields": f"{ship.shields}/{ship.max_shields}" if ship else None,
                "cargo": f"{ship.used_cargo:.0f}/{ship.max_cargo}" if ship else None,
                "credits": ship.credits if ship else 0,
                "jump_range": ship.jump_range if ship else 0,
                "speed": ship.speed if ship else 0,
                "attack": ship.attack_power if ship else 0,
                "sensor_range": ship.sensor_range if ship else 0,
            } if ship else None,
            "current_system": {
                "name": cell.system.name if cell and cell.system else None,
                "class": cell.system.system_class.name if cell and cell.system else None,
                "faction": cell.system.faction.name if cell and cell.system else None,
            } if cell and cell.system else None,
            "cargo_manifest": [
                {"good": item.good_key, "name": item.good.name,
                 "qty": item.quantity, "paid": item.purchase_price}
                for item in (ship.cargo_hold if ship else [])
            ],
            "tech": {
                "unlocked": list(tech.unlocked),
                "fuel_efficiency": tech.fuel_efficiency,
                "shield_regen": tech.shield_regen,
                "attack_bonus": tech.attack_bonus,
                "sensor_bonus": tech.sensor_bonus,
                "jump_bonus": tech.jump_bonus,
                "special": list(tech.special),
            },
            "npcs": [
                {"name": p.name, "id": p.id, "personality": getattr(self.ai_agents.get(p.id), 'profile', None) and self.ai_agents[p.id].profile.personality,
                 "ship": p.primary_ship.name if p.primary_ship else None,
                 "position": str(p.primary_ship.position) if p.primary_ship else None}
                for p in self.players.values() if p.id != player_id
            ],
        }

    def get_npc_roster(self, player_id: str) -> list[dict]:
        """Return NPCs visible to the player."""
        return self.get_game_state(player_id).get("npcs", [])

    def run_turn(self) -> None:
        """Process a turn for the primary player (p1)."""
        if "p1" in self.players:
            self.process_turn("p1")

