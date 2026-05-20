"""
Procedural galaxy map generation with fog of war.

Generates a hex-grid galaxy with star systems, terrain features,
wormhole pairs, and faction territories. Supports configurable
size, density, and seed for reproducible maps.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .core import (
    Position, TerrainType, SystemClass, Faction, DiscoveryType,
    TradeGood, TRADE_GOODS,
)


@dataclass
class SystemData:
    """Data for a single star system on the map."""
    position: Position
    name: str
    system_class: SystemClass
    faction: Faction
    population: int = 0
    tech_level: int = 1          # 1-10
    exports: list[str] = field(default_factory=list)   # TradeGood keys
    imports: list[str] = field(default_factory=list)    # TradeGood keys
    price_modifiers: dict[str, float] = field(default_factory=dict)  # good_key -> multiplier
    discovered: bool = False
    description: str = ""


@dataclass
class TerrainCell:
    """A single hex cell on the map."""
    position: Position
    terrain: TerrainType = TerrainType.EMPTY
    system: Optional[SystemData] = None
    wormhole_target: Optional[Position] = None
    hazard_damage: int = 0       # Damage taken when entering
    fuel_multiplier: float = 1.0  # Fuel cost multiplier for travel through


@dataclass
class GalaxyMap:
    """The full galaxy map with fog-of-war tracking per player."""
    radius: int                   # Map radius in hexes from center
    seed: int
    cells: dict[Position, TerrainCell] = field(default_factory=dict)
    wormhole_pairs: list[tuple[Position, Position]] = field(default_factory=list)
    faction_homeworlds: dict[Faction, Position] = field(default_factory=dict)

    # Per-player fog of war: player_id -> set of visible positions
    player_visibility: dict[str, set[Position]] = field(default_factory=dict)

    def in_bounds(self, pos: Position) -> bool:
        return pos.distance_to(Position(0, 0)) <= self.radius

    def get_cell(self, pos: Position) -> Optional[TerrainCell]:
        return self.cells.get(pos)

    def is_visible(self, pos: Position, player_id: str) -> bool:
        if player_id not in self.player_visibility:
            return False
        return pos in self.player_visibility[player_id]

    def reveal(self, pos: Position, player_id: str, radius: int = 0):
        """Reveal a position and optionally surrounding hexes."""
        if player_id not in self.player_visibility:
            self.player_visibility[player_id] = set()
        for p in pos.within(radius):
            if self.in_bounds(p):
                self.player_visibility[player_id].add(p)
                cell = self.cells.get(p)
                if cell and cell.system:
                    cell.system.discovered = True

    def visible_cells(self, player_id: str) -> dict[Position, TerrainCell]:
        """Return all cells visible to a player."""
        visible = self.player_visibility.get(player_id, set())
        return {p: self.cells[p] for p in visible if p in self.cells}

    def visible_systems(self, player_id: str) -> list[SystemData]:
        """Return all systems visible to a player."""
        result = []
        visible = self.player_visibility.get(player_id, set())
        for p in visible:
            cell = self.cells.get(p)
            if cell and cell.system:
                result.append(cell.system)
        return result


# ── Name Generation ───────────────────────────────────────────────────────────

_STAR_PREFIXES = [
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
    "Nova", "Proxima", "Ultima", "Omega", "Sigma", "Tau", "Rho", "Kappa",
    "Nexus", "Port", "New", "Old", "Far", "Deep", "High", "Low",
]

_STAR_SUFFIXES = [
    "Prime", "Major", "Minor", "Reach", "Haven", "Station", "Point",
    "Landing", "Crossing", "Gate", "Hold", "Rest", "Drift", "Veil",
    "Spire", "Cradle", "Forge", "Garden", "Sanctuary", "Citadel",
]

_STAR_SINGLE = [
    "Arcturus", "Betelgeuse", "Canopus", "Deneb", "Eridani", "Fomalhaut",
    "Ganymede", "Hyperion", "Icarus", "Janus", "Kepler", "Lyra",
    "Meridian", "Nereid", "Oberon", "Polaris", "Quasar", "Rigel",
    "Sirius", "Triton", "Umbra", "Vega", "Wolf", "Xenon", "Ymir", "Zenith",
    "Avalon", "Babylon", "Camelot", "Diaspora", "El Dorado", "Freeport",
    "Golgotha", "Haven", "Ithaka", "Jericho", "Kadesh", "Lemuria",
    "Mu", "Nirvana", "Olympus", "Paradise", "Qarth", "Rapture",
    "Shangri-La", "Thule", "Utopia", "Valhalla", "Xanadu", "Zion",
]


def _generate_system_name(rng: random.Random) -> str:
    if rng.random() < 0.4:
        return rng.choice(_STAR_SINGLE)
    prefix = rng.choice(_STAR_PREFIXES)
    suffix = rng.choice(_STAR_SUFFIXES)
    return f"{prefix} {suffix}"


# ── Map Generation ────────────────────────────────────────────────────────────

def generate_galaxy(
    radius: int = 20,
    system_density: float = 0.15,
    nebula_density: float = 0.05,
    asteroid_density: float = 0.04,
    anomaly_density: float = 0.02,
    num_wormholes: int = 3,
    num_factions: int = 4,
    seed: Optional[int] = None,
) -> GalaxyMap:
    """
    Generate a procedural galaxy map.

    Args:
        radius: Map radius in hexes from center (0,0).
        system_density: Fraction of hexes that are star systems.
        nebula_density: Fraction of hexes that are nebulae.
        asteroid_density: Fraction of hexes that are asteroid fields.
        anomaly_density: Fraction of hexes that are anomalies.
        num_wormholes: Number of wormhole pairs.
        num_factions: Number of active factions (2-5).
        seed: Random seed for reproducibility.
    """
    rng = random.Random(seed)
    galaxy = GalaxyMap(radius=radius, seed=seed if seed is not None else 0)

    center = Position(0, 0)
    all_positions = center.within(radius)

    # ── Step 1: Place terrain ──────────────────────────────────────────────
    for pos in all_positions:
        roll = rng.random()
        if pos == center:
            # Center is always a neutral space station
            galaxy.cells[pos] = TerrainCell(
                position=pos,
                terrain=TerrainType.SPACE_STATION,
            )
        elif roll < system_density:
            galaxy.cells[pos] = TerrainCell(
                position=pos,
                terrain=TerrainType.STAR_SYSTEM,
            )
        elif roll < system_density + nebula_density:
            galaxy.cells[pos] = TerrainCell(
                position=pos,
                terrain=TerrainType.NEBULA,
                fuel_multiplier=2.0,
            )
        elif roll < system_density + nebula_density + asteroid_density:
            galaxy.cells[pos] = TerrainCell(
                position=pos,
                terrain=TerrainType.ASTEROID_FIELD,
                hazard_damage=rng.randint(5, 15),
            )
        elif roll < system_density + nebula_density + asteroid_density + anomaly_density:
            galaxy.cells[pos] = TerrainCell(
                position=pos,
                terrain=TerrainType.ANOMALY,
            )
        else:
            galaxy.cells[pos] = TerrainCell(
                position=pos,
                terrain=TerrainType.EMPTY,
            )

    # ── Step 2: Place wormhole pairs ───────────────────────────────────────
    empty_positions = [p for p in all_positions
                       if galaxy.cells[p].terrain == TerrainType.EMPTY
                       and p.distance_to(center) > 3]
    rng.shuffle(empty_positions)

    for i in range(min(num_wormholes, len(empty_positions) // 2)):
        a = empty_positions[i * 2]
        b = empty_positions[i * 2 + 1]
        galaxy.cells[a].terrain = TerrainType.WORMHOLE
        galaxy.cells[a].wormhole_target = b
        galaxy.cells[b].terrain = TerrainType.WORMHOLE
        galaxy.cells[b].wormhole_target = a
        galaxy.wormhole_pairs.append((a, b))

    # ── Step 3: Place factions and their territories ───────────────────────
    factions = list(Faction)[:num_factions]
    # Pick homeworld positions far from center and each other
    candidates = [p for p in all_positions
                  if galaxy.cells[p].terrain == TerrainType.STAR_SYSTEM
                  and p.distance_to(center) > radius * 0.5]
    rng.shuffle(candidates)

    faction_positions: dict[Faction, Position] = {}
    for i, faction in enumerate(factions):
        if i < len(candidates):
            faction_positions[faction] = candidates[i]
            galaxy.faction_homeworlds[faction] = candidates[i]

    # ── Step 4: Populate star systems ──────────────────────────────────────
    system_positions = [p for p in all_positions
                        if galaxy.cells[p].terrain == TerrainType.STAR_SYSTEM]

    for pos in system_positions:
        # Determine faction ownership based on proximity
        owner = _determine_faction(pos, faction_positions, rng)

        # Determine system class
        dist_from_center = pos.distance_to(center)
        if pos in faction_positions.values():
            sys_class = SystemClass.CAPITAL
        elif dist_from_center < radius * 0.3:
            sys_class = rng.choice([SystemClass.COLONY, SystemClass.INDUSTRIAL])
        elif dist_from_center < radius * 0.6:
            sys_class = rng.choice([
                SystemClass.COLONY, SystemClass.OUTPOST,
                SystemClass.MINING, SystemClass.AGRICULTURAL,
            ])
        else:
            sys_class = rng.choice([
                SystemClass.OUTPOST, SystemClass.MINING,
                SystemClass.RESEARCH, SystemClass.RUINS,
            ])

        # Generate trade profile
        exports, imports, price_mods = _generate_trade_profile(sys_class, owner, rng)

        system = SystemData(
            position=pos,
            name=_generate_system_name(rng),
            system_class=sys_class,
            faction=owner,
            population=rng.randint(1000, 10_000_000),
            tech_level=rng.randint(1, 10),
            exports=exports,
            imports=imports,
            price_modifiers=price_mods,
            description=_system_description(sys_class, owner),
        )
        galaxy.cells[pos].system = system

    # ── Step 5: Place a few derelicts and pirate lairs ─────────────────────
    edge_positions = [p for p in all_positions
                      if galaxy.cells[p].terrain == TerrainType.EMPTY
                      and p.distance_to(center) > radius * 0.7]
    rng.shuffle(edge_positions)

    for i in range(min(3, len(edge_positions))):
        galaxy.cells[edge_positions[i]].terrain = TerrainType.PIRATE_LAIR

    for i in range(3, min(6, len(edge_positions))):
        galaxy.cells[edge_positions[i]].terrain = TerrainType.DERELICT

    return galaxy


def _determine_faction(
    pos: Position,
    faction_positions: dict[Faction, Position],
    rng: random.Random,
) -> Faction:
    """Determine which faction controls a system."""
    if not faction_positions:
        return Faction.FREE_TRADERS_GUILD

    # Find nearest faction
    nearest = min(faction_positions.items(), key=lambda x: pos.distance_to(x[1]))

    # Some randomness: 20% chance of being independent
    if rng.random() < 0.2:
        return Faction.FREE_TRADERS_GUILD

    return nearest[0]


def _generate_trade_profile(
    sys_class: SystemClass,
    faction: Faction,
    rng: random.Random,
) -> tuple[list[str], list[str], dict[str, float]]:
    """Generate what a system exports, imports, and price modifiers."""
    all_goods = list(TRADE_GOODS.keys())

    # Exports based on system class
    export_map = {
        SystemClass.CAPITAL: ["electronics", "machinery", "medicine"],
        SystemClass.COLONY: ["food", "textiles", "machinery"],
        SystemClass.OUTPOST: ["ore", "fuel_cells"],
        SystemClass.MINING: ["ore", "fuel_cells"],
        SystemClass.AGRICULTURAL: ["food", "luxury_food"],
        SystemClass.INDUSTRIAL: ["machinery", "electronics", "weapons"],
        SystemClass.RESEARCH: ["data_crystals", "medicine", "electronics"],
        SystemClass.RUINS: ["artifacts", "data_crystals"],
    }

    exports = list(export_map.get(sys_class, ["ore"]))
    # Add 0-2 random additional exports
    extra = [g for g in all_goods if g not in exports]
    rng.shuffle(extra)
    exports.extend(extra[:rng.randint(0, 2)])

    # Imports: goods not exported
    imports = [g for g in all_goods if g not in exports]
    rng.shuffle(imports)
    imports = imports[:rng.randint(2, 5)]

    # Price modifiers: exports are cheap, imports are expensive
    price_mods: dict[str, float] = {}
    for g in exports:
        price_mods[g] = round(rng.uniform(0.5, 0.8), 2)
    for g in imports:
        price_mods[g] = round(rng.uniform(1.2, 2.0), 2)

    return exports, imports, price_mods


def _system_description(sys_class: SystemClass, faction: Faction) -> str:
    descriptions = {
        SystemClass.CAPITAL: "A bustling hub of commerce and politics.",
        SystemClass.COLONY: "A thriving settlement with diverse opportunities.",
        SystemClass.OUTPOST: "A remote outpost on the frontier.",
        SystemClass.MINING: "Rich mineral deposits fuel the local economy.",
        SystemClass.AGRICULTURAL: "Endless fields feed the sector.",
        SystemClass.INDUSTRIAL: "Factories and shipyards dominate the skyline.",
        SystemClass.RESEARCH: "Cutting-edge research facilities hum with activity.",
        SystemClass.RUINS: "Ancient structures hint at a long-dead civilization.",
    }
    return descriptions.get(sys_class, "An unremarkable star system.")


# ── Multi-Position Player Starts ─────────────────────────────────────────────

def generate_player_starts(
    galaxy: GalaxyMap,
    num_players: int,
    min_distance: int = 8,
    rng: Optional[random.Random] = None,
) -> list[list[Position]]:
    """
    Generate multi-position starting locations for each player.

    Each player gets 2-3 starting positions (a home system and nearby
    outposts) clustered together but separated from other players.

    Args:
        galaxy: The generated galaxy map.
        num_players: Number of players.
        min_distance: Minimum distance between player clusters.
        rng: Random number generator.

    Returns:
        List of starting position lists, one per player.
    """
    if rng is None:
        rng = random.Random()

    # Find all star systems that could serve as starting locations
    center = Position(0, 0)
    candidates = [
        p for p, cell in galaxy.cells.items()
        if cell.terrain == TerrainType.STAR_SYSTEM
        and cell.system is not None
        and p.distance_to(center) > 3
    ]
    rng.shuffle(candidates)

    starts: list[list[Position]] = []
    used_positions: set[Position] = set()

    for player_idx in range(num_players):
        # Find a primary start far from other players
        primary = None
        for c in candidates:
            if c in used_positions:
                continue
            if all(c.distance_to(s) >= min_distance
                   for start_set in starts for s in start_set):
                primary = c
                break

        if primary is None:
            # Fallback: pick any unused candidate
            for c in candidates:
                if c not in used_positions:
                    primary = c
                    break

        if primary is None:
            break

        player_starts = [primary]
        used_positions.add(primary)

        # Add 1-2 secondary starts nearby
        nearby = [
            p for p in candidates
            if p not in used_positions
            and 2 <= p.distance_to(primary) <= 5
        ]
        rng.shuffle(nearby)
        for extra in nearby[:rng.randint(1, 2)]:
            player_starts.append(extra)
            used_positions.add(extra)

        starts.append(player_starts)

    return starts
