"""
Travel engine: navigation, fuel consumption, hazards, wormholes,
pathfinding, and travel events.

Handles all ship movement across the hex galaxy map.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional, Callable

from .core import (
    Position, TerrainType, DiscoveryType, EventType,
    HexDir,
)
from .map import GalaxyMap, TerrainCell
from .player import Ship


@dataclass
class TravelEvent:
    """Something that happens during travel."""
    event_type: EventType
    position: Position
    description: str
    damage_taken: int = 0
    fuel_lost: int = 0
    credits_gained: int = 0
    discovery: Optional[DiscoveryType] = None


@dataclass
class TravelResult:
    """Result of a travel attempt."""
    success: bool
    ship: Ship
    path: list[Position]
    events: list[TravelEvent] = field(default_factory=list)
    message: str = ""


@dataclass
class TravelOption:
    """A possible destination from the current position."""
    destination: Position
    distance: int
    fuel_cost: int
    terrain: TerrainType
    system_name: Optional[str] = None
    hazards: list[str] = field(default_factory=list)


class TravelEngine:
    """
    Handles all ship movement, fuel consumption, hazards, and travel events.
    """

    def __init__(
        self,
        galaxy: GalaxyMap,
        rng: Optional[random.Random] = None,
    ):
        self.galaxy = galaxy
        self.rng = rng or random.Random()

    # ── Single Jump ────────────────────────────────────────────────────────

    def travel(self, ship: Ship, destination: Position) -> TravelResult:
        """
        Attempt to move a ship from its current position to a destination.

        Validates jump range, fuel, and processes hazards/events along the way.
        """
        start = ship.position
        distance = start.distance_to(destination)

        # Validate
        if distance == 0:
            return TravelResult(False, ship, [], message="Already at destination.")
        if distance > ship.jump_range:
            return TravelResult(False, ship, [],
                                message=f"Destination is {distance} hexes away, "
                                        f"but jump range is only {ship.jump_range}.")

        # Calculate path
        path = start.line_to(destination)
        events: list[TravelEvent] = []

        # Calculate fuel cost
        fuel_cost = self._calculate_fuel_cost(path, ship)
        if ship.fuel < fuel_cost:
            return TravelResult(False, ship, path,
                                message=f"Need {fuel_cost} fuel, but only have {ship.fuel}.")

        # Process each cell along the path
        for i, pos in enumerate(path):
            if i == 0:
                continue  # Skip starting position

            cell = self.galaxy.get_cell(pos)
            if not cell:
                continue

            # Process terrain effects
            event = self._process_cell(pos, cell, ship)
            if event:
                events.append(event)

            # Check if ship was destroyed
            if ship.is_destroyed():
                return TravelResult(False, ship, path[:i+1], events,
                                    message="Ship destroyed during travel!")

        # Consume fuel
        ship.fuel -= fuel_cost

        # Move ship
        ship.position = destination

        # Reveal map around destination
        self.galaxy.reveal(destination, ship.owner_id, ship.sensor_range)

        # Check for discovery at destination
        dest_cell = self.galaxy.get_cell(destination)
        if dest_cell:
            discovery = self._check_discovery(dest_cell, ship)
            if discovery:
                events.append(discovery)

        return TravelResult(True, ship, path, events,
                            message=f"Arrived at {destination}.")

    def _calculate_fuel_cost(self, path: list[Position], ship: Ship) -> int:
        """Calculate total fuel cost for a path."""
        base_cost = len(path) - 1  # 1 fuel per hex
        total = 0
        for i, pos in enumerate(path):
            if i == 0:
                continue
            cell = self.galaxy.get_cell(pos)
            if cell:
                total += int(base_cost * cell.fuel_multiplier / max(1, len(path) - 1))
        return max(1, total)

    def _process_cell(
        self, pos: Position, cell: TerrainCell, ship: Ship
    ) -> Optional[TravelEvent]:
        """Process terrain effects for entering a cell. Returns event if notable."""
        terrain = cell.terrain

        if terrain == TerrainType.ASTEROID_FIELD:
            damage = cell.hazard_damage
            # Shield resistance
            if ship.shields > 0:
                absorbed = min(ship.shields, damage // 2)
                ship.shields -= absorbed
                damage -= absorbed
            ship.hull -= damage
            return TravelEvent(
                EventType.SOLAR_FLARE, pos,
                f"Asteroid field! Took {cell.hazard_damage} damage.",
                damage_taken=cell.hazard_damage,
            )

        elif terrain == TerrainType.NEBULA:
            # Drift chance
            if not ship.has_module("nebula"):
                if self.rng.random() < 0.3:
                    drift_dir = self.rng.choice(list(HexDir))
                    drift_pos = pos.neighbor(drift_dir)
                    ship.position = drift_pos
                    return TravelEvent(
                        EventType.NOTHING, pos,
                        "Nebula interference caused navigational drift!",
                    )

        elif terrain == TerrainType.BLACK_HOLE:
            ship.take_damage(20)
            return TravelEvent(
                EventType.SOLAR_FLARE, pos,
                "Gravitational shear from black hole! Heavy damage!",
                damage_taken=20,
            )

        elif terrain == TerrainType.WORMHOLE:
            if cell.wormhole_target and ship.has_module("wormhole"):
                ship.position = cell.wormhole_target
                return TravelEvent(
                    EventType.NOTHING, pos,
                    f"Wormhole stabilized! Jumped to {cell.wormhole_target}.",
                )
            elif cell.wormhole_target:
                # Unstable wormhole: chance of damage
                if self.rng.random() < 0.5:
                    ship.take_damage(10)
                    return TravelEvent(
                        EventType.SOLAR_FLARE, pos,
                        "Unstable wormhole transit caused damage!",
                        damage_taken=10,
                    )
                else:
                    ship.position = cell.wormhole_target
                    return TravelEvent(
                        EventType.NOTHING, pos,
                        f"Rode the wormhole to {cell.wormhole_target}!",
                    )

        elif terrain == TerrainType.PIRATE_LAIR:
            if self.rng.random() < 0.6:
                return self._pirate_encounter(pos, ship)

        elif terrain == TerrainType.DERELICT:
            return self._derelict_event(pos, ship)

        elif terrain == TerrainType.ANOMALY:
            return self._anomaly_event(pos, ship)

        return None

    def _check_discovery(
        self, cell: TerrainCell, ship: Ship
    ) -> Optional[TravelEvent]:
        """Check for discoveries when arriving at a new system."""
        if cell.terrain != TerrainType.STAR_SYSTEM:
            return None
        if not cell.system:
            return None
        if cell.system.discovered:
            return None

        # Roll for discovery
        roll = self.rng.random()
        if roll < 0.3:
            discovery = DiscoveryType.NOTHING
        elif roll < 0.5:
            discovery = DiscoveryType.TRADE_GOODS
            bonus_goods = self.rng.choice(list(cell.system.exports))
            ship.add_cargo(bonus_goods, self.rng.randint(1, 5))
        elif roll < 0.65:
            discovery = DiscoveryType.FUEL_CACHE
            ship.refuel(20)
        elif roll < 0.75:
            discovery = DiscoveryType.ANCIENT_TECH
            ship.credits += 500
        elif roll < 0.85:
            discovery = DiscoveryType.STAR_CHART
        elif roll < 0.92:
            discovery = DiscoveryType.ALIEN_ARTIFACT
            ship.credits += 1000
        elif roll < 0.97:
            discovery = DiscoveryType.HIDDEN_STATION
        else:
            discovery = DiscoveryType.PIRATE_AMBUSH
            return self._pirate_encounter(cell.position, ship)

        return TravelEvent(
            EventType.ANCIENT_SIGNAL, cell.position,
            f"Discovered: {discovery.name}!",
            discovery=discovery,
        )

    # ── Encounters ──────────────────────────────────────────────────────────

    def _pirate_encounter(self, pos: Position, ship: Ship) -> TravelEvent:
        """Handle a pirate attack."""
        pirate_power = self.rng.randint(10, 30)
        if ship.attack_power >= pirate_power:
            bounty = self.rng.randint(100, 500)
            ship.credits += bounty
            return TravelEvent(
                EventType.PIRATE_ATTACK, pos,
                f"Pirates defeated! Gained {bounty} credits in bounty.",
                credits_gained=bounty,
            )
        else:
            damage = pirate_power - ship.attack_power
            ship.take_damage(damage)
            lost_cargo = self.rng.randint(0, min(3, len(ship.cargo_hold)))
            for _ in range(lost_cargo):
                if ship.cargo_hold:
                    ship.cargo_hold.pop(self.rng.randint(0, len(ship.cargo_hold) - 1))
            return TravelEvent(
                EventType.PIRATE_ATTACK, pos,
                f"Pirates! Took {damage} damage and stole cargo!",
                damage_taken=damage,
            )

    def _derelict_event(self, pos: Position, ship: Ship) -> TravelEvent:
        """Salvage a derelict ship."""
        roll = self.rng.random()
        if roll < 0.4:
            credits = self.rng.randint(200, 1000)
            ship.credits += credits
            return TravelEvent(EventType.NOTHING, pos,
                               f"Salvaged {credits} credits from derelict.",
                               credits_gained=credits)
        elif roll < 0.7:
            ship.refuel(30)
            return TravelEvent(EventType.NOTHING, pos,
                               "Found fuel cells in the derelict.", fuel_lost=-30)
        elif roll < 0.9:
            good = self.rng.choice(list(TRADE_GOODS))
            ship.add_cargo(good, self.rng.randint(1, 3))
            return TravelEvent(EventType.NOTHING, pos,
                               f"Found cargo: {TRADE_GOODS[good].name}.")
        else:
            ship.take_damage(5)
            return TravelEvent(EventType.SOLAR_FLARE, pos,
                               "Derelict exploded! Took 5 damage.", damage_taken=5)

    def _anomaly_event(self, pos: Position, ship: Ship) -> TravelEvent:
        """Encounter a space anomaly."""
        roll = self.rng.random()
        if roll < 0.3:
            ship.refuel(50)
            return TravelEvent(EventType.NOTHING, pos,
                               "Anomaly recharged your fuel cells!")
        elif roll < 0.5:
            ship.repair(10)
            ship.recharge_shields(10)
            return TravelEvent(EventType.NOTHING, pos,
                               "Anomaly repaired your ship!")
        elif roll < 0.7:
            ship.take_damage(10)
            return TravelEvent(EventType.SOLAR_FLARE, pos,
                               "Anomaly discharged! Took 10 damage.", damage_taken=10)
        elif roll < 0.9:
            # Teleport to random system
            systems = [p for p, c in self.galaxy.cells.items()
                       if c.terrain == TerrainType.STAR_SYSTEM and p != pos]
            if systems:
                new_pos = self.rng.choice(systems)
                ship.position = new_pos
                return TravelEvent(EventType.NOTHING, pos,
                                   f"Anomaly teleported you to {new_pos}!")
        else:
            ship.credits += 2000
            return TravelEvent(EventType.NOTHING, pos,
                               "Anomaly contained valuable rare elements!",
                               credits_gained=2000)

    # ── Pathfinding ─────────────────────────────────────────────────────────

    def find_path(
        self,
        ship: Ship,
        destination: Position,
        max_jumps: int = 50,
    ) -> Optional[list[Position]]:
        """
        A* pathfinding for multi-jump routes.

        Returns a list of waypoints (positions to jump through) or None
        if no path exists within max_jumps.
        """
        import heapq

        start = ship.position
        if start == destination:
            return [start]

        # A* on hex grid
        frontier: list[tuple[float, Position]] = [(0, start)]
        came_from: dict[Position, Position] = {}
        cost_so_far: dict[Position, float] = {start: 0}

        while frontier:
            _, current = heapq.heappop(frontier)

            if current == destination:
                break

            for neighbor in current.neighbors():
                if not self.galaxy.in_bounds(neighbor):
                    continue

                cell = self.galaxy.get_cell(neighbor)
                move_cost = 1.0
                if cell:
                    if cell.terrain == TerrainType.BLACK_HOLE:
                        continue  # Impassable
                    move_cost = cell.fuel_multiplier
                    if cell.terrain == TerrainType.ASTEROID_FIELD:
                        move_cost += 0.5
                    elif cell.terrain == TerrainType.NEBULA:
                        move_cost += 0.3

                new_cost = cost_so_far[current] + move_cost

                if neighbor not in cost_so_far or new_cost < cost_so_far[neighbor]:
                    cost_so_far[neighbor] = new_cost
                    priority = new_cost + neighbor.distance_to(destination)
                    heapq.heappush(frontier, (priority, neighbor))
                    came_from[neighbor] = current

        if destination not in came_from and destination != start:
            return None

        # Reconstruct path
        path: list[Position] = [destination]
        current = destination
        while current != start:
            current = came_from[current]
            path.append(current)
        path.reverse()

        # Simplify to waypoints within jump range
        waypoints = self._simplify_path(path, ship.jump_range)

        return waypoints

    def _simplify_path(
        self, path: list[Position], jump_range: int
    ) -> list[Position]:
        """Reduce a hex-by-hex path to jump-range waypoints."""
        if len(path) <= 2:
            return path

        waypoints = [path[0]]
        i = 0
        while i < len(path) - 1:
            # Find furthest reachable point
            furthest = i + 1
            for j in range(i + 1, min(i + jump_range + 1, len(path))):
                if path[i].distance_to(path[j]) <= jump_range:
                    furthest = j
            waypoints.append(path[furthest])
            i = furthest

        return waypoints

    # ── Travel Options ──────────────────────────────────────────────────────

    def get_valid_destinations(self, ship: Ship) -> list[TravelOption]:
        """Get all systems within jump range."""
        options: list[TravelOption] = []
        current = ship.position

        for pos, cell in self.galaxy.cells.items():
            if pos == current:
                continue
            dist = current.distance_to(pos)
            if dist > ship.jump_range:
                continue
            if not self.galaxy.is_visible(pos, ship.owner_id):
                continue

            fuel_cost = self._calculate_fuel_cost(current.line_to(pos), ship)
            hazards = []
            if cell.terrain == TerrainType.ASTEROID_FIELD:
                hazards.append(f"Asteroids ({cell.hazard_damage} dmg)")
            elif cell.terrain == TerrainType.NEBULA:
                hazards.append("Nebula (drift risk)")
            elif cell.terrain == TerrainType.BLACK_HOLE:
                hazards.append("Black Hole!")
            elif cell.terrain == TerrainType.PIRATE_LAIR:
                hazards.append("Pirate activity")

            system_name = cell.system.name if cell.system else None

            options.append(TravelOption(
                destination=pos,
                distance=dist,
                fuel_cost=fuel_cost,
                terrain=cell.terrain,
                system_name=system_name,
                hazards=hazards,
            ))

        # Sort by distance
        options.sort(key=lambda o: o.distance)
        return options

    def get_travel_options(self, ship: Ship) -> list[TravelOption]:
        """Alias for get_valid_destinations."""
        return self.get_valid_destinations(ship)


# Need this import at module level for _derelict_event
from .core import TRADE_GOODS
