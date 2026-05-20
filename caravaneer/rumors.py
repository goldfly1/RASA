"""
Rumors and merchant personality system.

Deterministic rumor generation and propagation.
Merchants have personalities that affect dialogue, prices, and information sharing.
LLM hook provided but not required.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional, Callable

from .core import Faction, Position, SystemClass, EventType
from .map import SystemData
from .player import Player


# ═══════════════════════════════════════════════════════════════════════════════
# Rumors
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Rumor:
    """A piece of information floating around the galaxy."""
    text: str
    truth_value: float           # 0.0 = completely false, 1.0 = completely true
    source_system: Position
    expiry_turn: int             # Turn when this rumor dies
    category: str = "general"    # market, pirate, discovery, player, faction
    related_system: Optional[Position] = None
    related_good: Optional[str] = None


class RumorEngine:
    """Generates, spreads, and ages rumors."""

    def __init__(self, rng: Optional[random.Random] = None):
        self.rng = rng or random.Random()
        self.active_rumors: list[Rumor] = []
        self._turn = 0

    def tick(self, turn: int):
        """Age rumors and remove expired ones."""
        self._turn = turn
        self.active_rumors = [r for r in self.active_rumors if r.expiry_turn > turn]

    def generate_market_rumor(self, system: SystemData, good_key: str, trend: str) -> Rumor:
        """Generate a market price rumor."""
        texts = [
            f"Prices for {good_key} are {trend} at {system.name}.",
            f"Merchants say {system.name} is {trend} for {good_key}.",
            f"Word is {good_key} is {trend} near {system.name}.",
        ]
        return Rumor(
            text=self.rng.choice(texts),
            truth_value=0.85,
            source_system=system.position,
            expiry_turn=self._turn + self.rng.randint(3, 8),
            category="market",
            related_system=system.position,
            related_good=good_key,
        )

    def generate_pirate_rumor(self, system: SystemData) -> Rumor:
        """Generate a pirate sighting rumor."""
        texts = [
            f"Pirates spotted near {system.name}!",
            f"A trader was hit by pirates near {system.name}.",
            f"Avoid the {system.name} sector — pirate activity reported.",
        ]
        return Rumor(
            text=self.rng.choice(texts),
            truth_value=0.75,
            source_system=system.position,
            expiry_turn=self._turn + self.rng.randint(2, 6),
            category="pirate",
            related_system=system.position,
        )

    def generate_discovery_rumor(self, system: SystemData, discovery_type: str) -> Rumor:
        """Generate a discovery rumor."""
        texts = [
            f"Explorers found {discovery_type} at {system.name}!",
            f"Something strange was discovered near {system.name}...",
            f"Rumors of {discovery_type} in the {system.name} sector.",
        ]
        return Rumor(
            text=self.rng.choice(texts),
            truth_value=0.70,
            source_system=system.position,
            expiry_turn=self._turn + self.rng.randint(4, 10),
            category="discovery",
            related_system=system.position,
        )

    def generate_player_rumor(self, player: Player, action: str, system: SystemData) -> Rumor:
        """Generate a rumor about a player's actions."""
        texts = [
            f"{player.name} was seen {action} at {system.name}.",
            f"Word is {player.name} is {action} near {system.name}.",
            f"Keep an eye on {player.name} — they've been {action}.",
        ]
        return Rumor(
            text=self.rng.choice(texts),
            truth_value=0.90,
            source_system=system.position,
            expiry_turn=self._turn + self.rng.randint(3, 7),
            category="player",
            related_system=system.position,
        )

    def get_rumors_at(self, pos: Position, radius: int = 2) -> list[Rumor]:
        """Get rumors relevant to a position and nearby systems."""
        nearby = pos.within(radius)
        return [r for r in self.active_rumors if r.source_system in nearby or r.related_system in nearby]

    def add_rumor(self, rumor: Rumor):
        self.active_rumors.append(rumor)


# ═══════════════════════════════════════════════════════════════════════════════
# Merchant Personality
# ═══════════════════════════════════════════════════════════════════════════════

class MerchantPersonality:
    """Personality archetypes for starport merchants."""
    SNARKY = "snarky"
    GREEDY = "greedy"
    PARANOID = "paranoid"
    HELPFUL = "helpful"
    SHADY = "shady"
    JOVIAL = "jovial"
    ALL = [SNARKY, GREEDY, PARANOID, HELPFUL, SHADY, JOVIAL]


@dataclass
class Merchant:
    """A merchant at a star system."""
    name: str
    personality: str
    faction: Faction
    system_pos: Position
    # Price modifiers based on personality
    buy_markup: float = 1.0    # Multiply sell prices to player
    sell_discount: float = 1.0 # Multiply buy prices from player
    # Information sharing
    rumor_chance: float = 0.5  # Chance to share a rumor
    lie_chance: float = 0.1    # Chance the rumor is fabricated
    # Dialogue templates (deterministic fallback)
    greetings: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.greetings:
            self.greetings = self._default_greetings()

    def _default_greetings(self) -> list[str]:
        templates = {
            MerchantPersonality.SNARKY: [
                "Oh great, another captain with more ambition than credits.",
                "Try not to break anything. The warranty expired last century.",
                "What do you want? I'm counting my losses.",
            ],
            MerchantPersonality.GREEDY: [
                "Everything has a price. Everything.",
                "Show me credits and I'll show you miracles.",
                "My prices are fair. My definitions may vary.",
            ],
            MerchantPersonality.PARANOID: [
                "Keep your hands where I can see them.",
                "Are you with THEM? ...Never mind. What do you need?",
                "I've got sensors everywhere. Don't try anything.",
            ],
            MerchantPersonality.HELPFUL: [
                "Welcome! Let me know if you need anything.",
                "Safe travels, captain. What can I do for you?",
                "Always happy to help a fellow spacer.",
            ],
            MerchantPersonality.SHADY: [
                "Psst. You look like someone who appreciates... discretion.",
                "I have things that aren't on the manifest. Interested?",
                "No questions asked. No answers given.",
            ],
            MerchantPersonality.JOVIAL: [
                "Hah! Another soul brave enough to face the void!",
                "Pull up a chair, friend! The galaxy's wild out there!",
                "Drinks are on me! ...Well, metaphorically.",
            ],
        }
        return templates.get(self.personality, templates[MerchantPersonality.HELPFUL])

    def greet(self, player: Player) -> str:
        """Get a greeting for a player."""
        import random
        base = random.choice(self.greetings)
        # Modify based on reputation
        rep = player.get_reputation(self.faction)
        if rep < -30:
            return f"{base} (They eye you suspiciously — your reputation with {self.faction.name} is poor.)"
        elif rep > 30:
            return f"{base} (They smile warmly — you're favored by {self.faction.name}.)"
        return base

    def modify_price(self, base_price: int, action: str) -> int:
        """Modify a price based on merchant personality.
        action is 'buy' (player buying from merchant) or 'sell' (player selling to merchant).
        """
        if action == "buy":
            return int(base_price * self.buy_markup)
        else:
            return int(base_price * self.sell_discount)

    def should_share_rumor(self) -> bool:
        import random
        return random.random() < self.rumor_chance

    def fabricate_rumor(self, engine: RumorEngine) -> Optional[Rumor]:
        """Generate a fake rumor."""
        import random
        if random.random() < self.lie_chance:
            texts = [
                "I heard the Krax Empire is building a superweapon...",
                "Someone told me there's a hidden gold cache at sector 0,0.",
                "The Terran Alliance fleet was destroyed last week. Or was it?",
                "Ancient technology was found in the nebula! Definitely. Trust me.",
            ]
            return Rumor(
                text=random.choice(texts),
                truth_value=0.0,
                source_system=self.system_pos,
                expiry_turn=engine._turn + random.randint(2, 4),
                category="general",
            )
        return None


def generate_merchant(system: SystemData, rng: Optional[random.Random] = None) -> Merchant:
    """Generate a merchant for a star system."""
    rng = rng or random.Random()
    personality = rng.choice(MerchantPersonality.ALL)
    names = {
        MerchantPersonality.SNARKY: ["Grix", "Varn", "Zel", "Mox"],
        MerchantPersonality.GREEDY: ["Coin", "Profit", "Goldhand", "Tally"],
        MerchantPersonality.PARANOID: ["Watch", "Scan", "Safe", "Lock"],
        MerchantPersonality.HELPFUL: ["Aid", "Guide", "Beacon", "Help"],
        MerchantPersonality.SHADY: ["Shadow", "Whisper", "Void", "Ghost"],
        MerchantPersonality.JOVIAL: ["Cheer", "Laugh", "Bright", "Joy"],
    }
    name = rng.choice(names.get(personality, ["Merchant"]))

    # Personality affects pricing
    buy_markup = 1.0
    sell_discount = 1.0
    rumor_chance = 0.5
    lie_chance = 0.1

    if personality == MerchantPersonality.GREEDY:
        buy_markup = rng.uniform(1.1, 1.3)
        sell_discount = rng.uniform(0.7, 0.9)
    elif personality == MerchantPersonality.HELPFUL:
        buy_markup = rng.uniform(0.9, 1.0)
        sell_discount = rng.uniform(1.0, 1.1)
        rumor_chance = 0.7
    elif personality == MerchantPersonality.SHADY:
        buy_markup = rng.uniform(1.0, 1.2)
        sell_discount = rng.uniform(0.8, 1.0)
        rumor_chance = 0.6
        lie_chance = 0.3
    elif personality == MerchantPersonality.PARANOID:
        rumor_chance = 0.2
        lie_chance = 0.05
    elif personality == MerchantPersonality.JOVIAL:
        rumor_chance = 0.8
        lie_chance = 0.15
    elif personality == MerchantPersonality.SNARKY:
        buy_markup = rng.uniform(1.05, 1.15)

    return Merchant(
        name=name,
        personality=personality,
        faction=system.faction,
        system_pos=system.position,
        buy_markup=buy_markup,
        sell_discount=sell_discount,
        rumor_chance=rumor_chance,
        lie_chance=lie_chance,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LLM Hook (optional)
# ═══════════════════════════════════════════════════════════════════════════════

async def llm_merchant_dialogue(
    merchant: Merchant,
    player: Player,
    context: str,
    ollama_url: str = "http://127.0.0.1:11434",
    model: str = "llama3",
) -> str:
    """
    Generate merchant dialogue via Ollama.
    Falls back to deterministic greeting if LLM fails.
    """
    import aiohttp
    rep = player.get_reputation(merchant.faction)
    prompt = (
        f"You are {merchant.name}, a {merchant.personality} merchant in a space trading game. "
        f"Your faction is {merchant.faction.name}. "
        f"The player, {player.name}, has reputation {rep} with your faction. "
        f"Context: {context}. "
        f"Respond in 1-2 sentences in character."
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{ollama_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                return data.get("response", merchant.greet(player))
    except Exception:
        return merchant.greet(player)
