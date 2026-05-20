"""
Caravaneer to the Stars — a space trading and exploration game engine.
Based on the Merchant of Venus ruleset, extended with fog of war,
multi-position starts, procedural maps, and open-ended victory.
"""

__version__ = "0.1.0"

from .game import Game, GameConfig, TurnLog
from .core import (
    Position, HexDir, TerrainType, SystemClass, Faction,
    DiscoveryType, EventType, VictoryType, GoodCategory,
    TradeGood, TRADE_GOODS, ShipClass, ShipTemplate, SHIP_TEMPLATES,
    ModuleSlot, ModuleTemplate, MODULE_TEMPLATES,
)
from .map import GalaxyMap, SystemData, TerrainCell, generate_galaxy, generate_player_starts
from .player import Ship, Player, CargoItem, ShipModule, create_starting_ship
from .travel import TravelEngine, TravelResult, TravelOption, TravelEvent
from .economy import Economy, TradeResult, MarketEvent
