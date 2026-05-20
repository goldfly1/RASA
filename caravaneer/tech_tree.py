"""
Faction tech trees — inspired by deep 4X progression without copying any specific game.

Each faction has unique technology branches. Techs unlock at reputation thresholds
giving passive bonuses, new modules, or special abilities. Players and NPCs both
progress through the same trees.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Callable

from .core import Faction, ModuleSlot, MODULE_TEMPLATES, TradeGood, TRADE_GOODS
from .player import Ship, Player


class TechBranch(Enum):
    PROPULSION = auto()
    DEFENSE = auto()
    WEAPONS = auto()
    SENSORS = auto()
    INDUSTRY = auto()
    ESPIONAGE = auto()


@dataclass
class Technology:
    """A single technology in a faction tree."""
    key: str
    name: str
    faction: Faction
    branch: TechBranch
    tier: int                      # 1-5, higher = more advanced
    reputation_req: int            # Faction reputation needed
    cost: int                      # Credits to unlock
    description: str
    # Effects
    module_unlock: Optional[str] = None     # Unlocks a new MODULE_TEMPLATES key
    passive_bonus: dict[str, float] = field(default_factory=dict)  # e.g. {"fuel_efficiency": 0.9}
    special_ability: Optional[str] = None  # e.g. "bounty_scanner", "smuggling_compartment"
    prerequisite: Optional[str] = None      # Key of prerequisite tech


@dataclass
class TechProgress:
    """Tracks a player/NPC's tech progression."""
    unlocked: set[str] = field(default_factory=set)
    # Passive bonuses currently active
    fuel_efficiency: float = 1.0     # Multiply fuel costs by this
    shield_regen: int = 0            # Extra shield recharge per turn
    cargo_efficiency: float = 1.0  # Cargo weight multiplier
    price_bonus: float = 0.0       # Buy discount / sell bonus (%)
    attack_bonus: int = 0          # Flat attack power bonus
    sensor_bonus: int = 0          # Flat sensor range bonus
    jump_bonus: int = 0            # Flat jump range bonus
    flee_bonus: float = 0.0        # Flee chance bonus
    special: set[str] = field(default_factory=set)  # Active special abilities


def compute_progress(unlocked: set[str], all_techs: dict[str, Technology]) -> TechProgress:
    """Recalculate TechProgress from a set of unlocked tech keys."""
    progress = TechProgress(unlocked=set(unlocked))
    for key in unlocked:
        tech = all_techs.get(key)
        if not tech:
            continue
        for k, v in tech.passive_bonus.items():
            if k == "fuel_efficiency":
                progress.fuel_efficiency *= v
            elif k == "shield_regen":
                progress.shield_regen += int(v)
            elif k == "cargo_efficiency":
                progress.cargo_efficiency *= v
            elif k == "price_bonus":
                progress.price_bonus += v
            elif k == "attack_bonus":
                progress.attack_bonus += int(v)
            elif k == "sensor_bonus":
                progress.sensor_bonus += int(v)
            elif k == "jump_bonus":
                progress.jump_bonus += int(v)
            elif k == "flee_bonus":
                progress.flee_bonus += v
        if tech.special_ability:
            progress.special.add(tech.special_ability)
    return progress


# ═══════════════════════════════════════════════════════════════════════════════
# Faction Tech Trees
# ═══════════════════════════════════════════════════════════════════════════════

TECH_TREES: dict[str, Technology] = {}

def _add(t: Technology):
    TECH_TREES[t.key] = t

# ── Terran Alliance ──
_add(Technology("ta_shield_harmonics", "Shield Harmonics", Faction.TERRAN_ALLIANCE, TechBranch.DEFENSE, 1, 10, 3000,
      "Advanced shield frequency tuning. +5 shields.", passive_bonus={"shield_regen": 5}))
_add(Technology("ta_reinforced_bulkheads", "Reinforced Bulkheads", Faction.TERRAN_ALLIANCE, TechBranch.DEFENSE, 2, 25, 6000,
      "Hull reinforcement using Terran naval alloys. +10 hull max.", prerequisite="ta_shield_harmonics",
      passive_bonus={"attack_bonus": 0}))  # Placeholder — hull bonus applied in ship calc
_add(Technology("ta_overcharged_emitters", "Overcharged Emitters", Faction.TERRAN_ALLIANCE, TechBranch.WEAPONS, 2, 20, 5500,
      "Weapon capacitor overcharge. +5 attack.", passive_bonus={"attack_bonus": 5}))
_add(Technology("ta_navigational_beacons", "Navigational Beacons", Faction.TERRAN_ALLIANCE, TechBranch.SENSORS, 1, 5, 2500,
      "Network of deep-space buoys. +1 sensor range.", passive_bonus={"sensor_bonus": 1}))
_add(Technology("ta_efficient_drives", "Efficient Drives", Faction.TERRAN_ALLIANCE, TechBranch.PROPULSION, 1, 15, 4000,
      "Terran engineering reduces fuel consumption by 10%.", passive_bonus={"fuel_efficiency": 0.90}))
_add(Technology("ta_diplomatic_channels", "Diplomatic Channels", Faction.TERRAN_ALLIANCE, TechBranch.ESPIONAGE, 3, 40, 8000,
      "Official trade licenses give 5% better prices.", passive_bonus={"price_bonus": 0.05}, special_ability="diplomatic_immunity"))
_add(Technology("ta_aegis_matrix", "Aegis Matrix", Faction.TERRAN_ALLIANCE, TechBranch.DEFENSE, 4, 60, 15000,
      "Integrated defense grid. Module unlock.", prerequisite="ta_reinforced_bulkheads",
      module_unlock="shield_mk2", special_ability="point_defense"))

# ── Centauri Collective ──
_add(Technology("cc_modular_cargo", "Modular Cargo Systems", Faction.CENTAURI_COLLECTIVE, TechBranch.INDUSTRY, 1, 10, 3000,
      "Expandable cargo holds. Cargo weight reduced 10%.", passive_bonus={"cargo_efficiency": 0.90}))
_add(Technology("cc_market_algorithms", "Market Algorithms", Faction.CENTAURI_COLLECTIVE, TechBranch.INDUSTRY, 2, 25, 5000,
      "Predictive trading models. 5% better prices.", passive_bonus={"price_bonus": 0.05}))
_add(Technology("cc_fast_courier_engines", "Fast Courier Engines", Faction.CENTAURI_COLLECTIVE, TechBranch.PROPULSION, 1, 15, 4500,
      "Speed-optimized drives. +1 jump range.", passive_bonus={"jump_bonus": 1}))
_add(Technology("cc_sensor_network", "Sensor Network", Faction.CENTAURI_COLLECTIVE, TechBranch.SENSORS, 2, 20, 6000,
      "Distributed sensor arrays. +2 sensor range.", passive_bonus={"sensor_bonus": 2}))
_add(Technology("cc_contraband_masking", "Contraband Masking", Faction.CENTAURI_COLLECTIVE, TechBranch.ESPIONAGE, 3, 35, 9000,
      "Hide illegal goods from standard scans.", special_ability="smuggling_compartment"))
_add(Technology("cc_trade_monopoly", "Trade Monopoly", Faction.CENTAURI_COLLECTIVE, TechBranch.INDUSTRY, 4, 55, 14000,
      "Corner market techniques. 10% better prices.", prerequisite="cc_market_algorithms",
      passive_bonus={"price_bonus": 0.10}, special_ability="market_manipulation"))

# ── Krax Empire ──
_add(Technology("kr_plasma_weaving", "Plasma Weaving", Faction.KRAX_EMPIRE, TechBranch.WEAPONS, 1, 10, 3500,
      "Krax plasma-forged weaponry. +5 attack.", passive_bonus={"attack_bonus": 5}))
_add(Technology("kr_armor_plating", "Heavy Armor Plating", Faction.KRAX_EMPIRE, TechBranch.DEFENSE, 1, 15, 4000,
      "Ablative armor layers. Damage reduction concept.", passive_bonus={"shield_regen": 0}))  # hull bonus
_add(Technology("kr_intimidation_tactics", "Intimidation Tactics", Faction.KRAX_EMPIRE, TechBranch.ESPIONAGE, 2, 20, 5000,
      "Fear-based negotiation. 5% better sell prices, worse buy prices.", passive_bonus={"price_bonus": 0.03}))
_add(Technology("kr_berserker_protocols", "Berserker Protocols", Faction.KRAX_EMPIRE, TechBranch.WEAPONS, 3, 35, 8000,
      "Automated combat AI. +10 attack.", prerequisite="kr_plasma_weaving",
      passive_bonus={"attack_bonus": 10}, special_ability="berserker_mode"))
_add(Technology("kr_salvage_ops", "Salvage Operations", Faction.KRAX_EMPIRE, TechBranch.INDUSTRY, 2, 25, 6000,
      "Recover extra loot from destroyed ships.", special_ability="salvage_bonus"))
_add(Technology("kr_dreadnought_design", "Dreadnought Design", Faction.KRAX_EMPIRE, TechBranch.DEFENSE, 4, 50, 12000,
      "Module unlock: heavy weapon systems.", prerequisite="kr_armor_plating",
      module_unlock="plasma_turret", special_ability="overwhelming_force"))

# ── Free Traders Guild ──
_add(Technology("ftg_silent_running", "Silent Running", Faction.FREE_TRADERS_GUILD, TechBranch.ESPIONAGE, 1, 10, 3000,
      "Reduce sensor signature. +10% flee chance.", passive_bonus={"flee_bonus": 0.10}))
_add(Technology("ftg_hidden_compartments", "Hidden Compartments", Faction.FREE_TRADERS_GUILD, TechBranch.INDUSTRY, 1, 5, 2500,
      "Smuggling holds. More cargo in same space.", passive_bonus={"cargo_efficiency": 0.85}))
_add(Technology("ftg_hot_engines", "Hot Engines", Faction.FREE_TRADERS_GUILD, TechBranch.PROPULSION, 2, 20, 5000,
      "Overclocked drives. +1 speed, +1 jump.", passive_bonus={"jump_bonus": 1}))
_add(Technology("ftg_underworld_contacts", "Underworld Contacts", Faction.FREE_TRADERS_GUILD, TechBranch.ESPIONAGE, 2, 25, 6000,
      "Access to black markets. Illegal goods treated as legal for pricing.", special_ability="black_market_access"))
_add(Technology("ftg_pirate_code", "Pirate Code", Faction.FREE_TRADERS_GUILD, TechBranch.ESPIONAGE, 3, 40, 10000,
      "Pirates may ignore you. Bounty scanning immunity.", prerequisite="ftg_underworld_contacts",
      special_ability="pirate_immunity"))
_add(Technology("ftg_ghost_ship", "Ghost Ship", Faction.FREE_TRADERS_GUILD, TechBranch.ESPIONAGE, 4, 60, 16000,
      "Advanced cloaking module unlock.", prerequisite="ftg_silent_running",
      module_unlock="cloaking_device", special_ability="full_cloak"))

# ── Void Syndicate ──
_add(Technology("vs_hacking_suite", "Hacking Suite", Faction.VOID_SYNDICATE, TechBranch.ESPIONAGE, 1, 10, 3500,
      "Electronic warfare tools. Can disrupt enemy shields.", special_ability="shield_hack"))
_add(Technology("vs_dark_matter_sensors", "Dark Matter Sensors", Faction.VOID_SYNDICATE, TechBranch.SENSORS, 1, 15, 4000,
      "See through nebulas. +2 sensor range.", passive_bonus={"sensor_bonus": 2}, special_ability="nebula_pierce"))
_add(Technology("vs_wormhole_breaching", "Wormhole Breaching", Faction.VOID_SYNDICATE, TechBranch.PROPULSION, 2, 25, 6000,
      "Force wormholes to stay open. Safe transit without stabilizer.", special_ability="wormhole_breach"))
_add(Technology("vs_nightmare_weapons", "Nightmare Weapons", Faction.VOID_SYNDICATE, TechBranch.WEAPONS, 3, 35, 9000,
      "Psychological warfare weapons. +8 attack.", passive_bonus={"attack_bonus": 8}, special_ability="fear_projection"))
_add(Technology("vs_reality_anchor", "Reality Anchor", Faction.VOID_SYNDICATE, TechBranch.DEFENSE, 4, 50, 13000,
      "Immunity to anomaly effects. +10 shields.", prerequisite="vs_dark_matter_sensors",
      passive_bonus={"shield_regen": 10}, special_ability="anomaly_immunity"))


# ═══════════════════════════════════════════════════════════════════════════════
# Tech Tree Manager
# ═══════════════════════════════════════════════════════════════════════════════

class TechTreeManager:
    """Manages tech unlocks for all players/NPCs."""

    def __init__(self):
        self.player_progress: dict[str, TechProgress] = {}
        self.player_unlocked: dict[str, set[str]] = {}

    def get_progress(self, player_id: str) -> TechProgress:
        """Get current tech progress for a player."""
        if player_id not in self.player_progress:
            unlocked = self.player_unlocked.get(player_id, set())
            self.player_progress[player_id] = compute_progress(unlocked, TECH_TREES)
        return self.player_progress[player_id]

    def can_unlock(self, player: Player, tech_key: str) -> tuple[bool, str]:
        """Check if a player can unlock a technology. Returns (ok, reason)."""
        tech = TECH_TREES.get(tech_key)
        if not tech:
            return False, f"Unknown tech: {tech_key}"
        if tech_key in self.player_unlocked.get(player.id, set()):
            return False, "Already unlocked."
        ship = player.primary_ship
        if not ship:
            return False, "No ship."
        if ship.credits < tech.cost:
            return False, f"Need {tech.cost} credits, have {ship.credits}."
        rep = player.get_reputation(tech.faction)
        if rep < tech.reputation_req:
            return False, f"Need {tech.reputation_req} rep with {tech.faction.name}, have {rep}."
        if tech.prerequisite and tech.prerequisite not in self.player_unlocked.get(player.id, set()):
            return False, f"Requires {TECH_TREES[tech.prerequisite].name}."
        return True, ""

    def unlock(self, player: Player, tech_key: str) -> tuple[bool, str]:
        """Unlock a technology for a player. Returns (success, message)."""
        ok, reason = self.can_unlock(player, tech_key)
        if not ok:
            return False, reason
        tech = TECH_TREES[tech_key]
        ship = player.primary_ship
        if ship is None:
            return False, "No ship."
        ship.credits -= tech.cost
        if player.id not in self.player_unlocked:
            self.player_unlocked[player.id] = set()
        self.player_unlocked[player.id].add(tech_key)
        # Recalculate progress
        self.player_progress[player.id] = compute_progress(self.player_unlocked[player.id], TECH_TREES)
        # If module unlock, install it
        if tech.module_unlock and tech.module_unlock in MODULE_TEMPLATES:
            from .player import ShipModule
            mod = ShipModule(template_key=tech.module_unlock, slot=MODULE_TEMPLATES[tech.module_unlock].slot)
            ship.modules.append(mod)
        return True, f"Unlocked {tech.name}!"

    def available_techs(self, player: Player) -> list[Technology]:
        """List all techs this player could potentially unlock, sorted by faction."""
        result = []
        for tech in TECH_TREES.values():
            ok, _ = self.can_unlock(player, tech.key)
            if ok:
                result.append(tech)
        return result

    def faction_progress(self, player_id: str, faction: Faction) -> list[str]:
        """List unlocked tech keys for a specific faction."""
        unlocked = self.player_unlocked.get(player_id, set())
        return [k for k in unlocked if TECH_TREES[k].faction == faction]
