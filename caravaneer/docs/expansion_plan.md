# Caravaneer Expansion Plan — Combat, AI, Tech, Rumors

## Core Philosophy
Build a competitive single-player experience with deterministic ghost opponents. 
The hook: every galaxy is alive — NPCs remember you, factions have grudges, 
merchants have personalities, and rumors spread. LLM can be layered on top for 
flavor text and dynamic dialogue, but the game is rock-solid without it.

## 1. Combat System (combat.py)
- **CombatResult**: winner, loser, damage dealt, loot gained, ship destroyed flag
- **CombatEngine**: resolve_ship_vs_ship(attacker, defender) -> CombatResult
  - Turn-based within a single macro-turn: attacker fires first
  - Damage = attack_power - (defender.shields * 0.5). Shields absorb, spill to hull.
  - Weapons have accuracy based on attacker speed vs defender speed.
  - If defender survives, they get a return shot.
  - Flee: 50% base chance, modified by speed difference + engine modules.
  - After combat: shields on both ships reduced by battle. Winner loots 20% of loser's cargo/credits if ship destroyed.
- **Pirate encounter**: entering PIRATE_LAIR = auto-combat vs generated pirate ship.
- **Player-initiated attack**: can attack any ship in the same hex (NPC or pirate).

## 2. Deterministic AI Agent (ai_agent.py)
Each NPC gets an AI profile:
- **Personality**: TRADER (profit-maximizing), EXPLORER (discovery-seeking), PIRATE (prey on weak), SMUGGLER (illegal goods, high risk/reward)
- **Memory**: remembers prices seen at each system, remembers player reputation, remembers profitable routes
- **Decision cycle per turn**:
  1. Evaluate threats (pirates nearby, low hull/fuel -> flee to safe system)
  2. Evaluate opportunities (arbitrage, discovery, prey)
  3. Select action: travel to best destination, trade, attack/flee
  4. Tech tree progression: NPCs accumulate credits and buy modules from faction-specific trees
- **Risk model**: fuel threshold (never travel below 20% fuel), hull threshold (never enter asteroid fields below 30% hull), combat threshold (flee if attack < defender's shields + hull)
- **Route scoring**: score = profit_potential / (fuel_cost + risk_penalty + distance_penalty)

## 3. Faction Tech Trees (tech_tree.py)
- **FactionTechnology**: unlocked at reputation thresholds, costs credits
- Each faction has unique module branches:
  - Terran Alliance: shield tech, reliable engines, law-abiding sensors
  - Centauri Collective: trade efficiency, cargo expansion, diplomatic comms
  - Krax Empire: weapon tech, armor plating, fear-based intimidation modules
  - Free Traders Guild: smuggling holds, stealth, fast engines
  - Void Syndicate: cloaking, hacking, contraband scanners
- **Player/NPC progression**: spend credits + meet reputation req to unlock. Unlocks persist for that ship.
- Techs provide passive bonuses or unlock new module types in the economy.

## 4. Reputation System Upgrade (reputation.py — integrated into game.py)
- Reputation now has CONSEQUENCES:
  - Below -50: faction ships may attack on sight
  - Above +50: faction gives discounts on tech, safe harbor
  - Trading with enemy of a faction = reputation hit with that faction
  - Destroying faction ships = massive reputation hit
  - Helping faction ships (attacking their enemies) = reputation gain
- **Bounties**: factions post bounties on hated players/NPCs. Collect by destroying them.
- **Safe harbors**: some systems refuse service to hated players.

## 5. Rumors & Merchant Personality (rumors.py)
- **Rumor**: text + truth_value (0-1) + expiry_turns. Spread between systems.
- Generated from:
  - Market events ("Gold rush on Xanadu!")
  - Pirate sightings ("Pirates spotted near Theta Major")
  - Player actions ("Someone's been selling slaves at Gamma Reach...")
  - Discovery events ("Ancient ruins found at sector 12,-3")
- **Merchant NPCs**: each star system has a merchant with personality (snarky, greedy, paranoid, helpful). Affects:
  - Greeting text
  - Price haggling (personality modifies base prices)
  - Information sharing (some merchants give rumors, others lie)
- **LLM hook point**: merchant_dialogue(merchant, player, context) -> can be swapped for LLM-generated text. Deterministic fallback always present.

## 6. Integration Points
- game.py: new phase COMBAT_PHASE before trade. NPC AI replaces _npc_act.
- player.py: add tech_unlocks set, reputation_consequences helper.
- economy.py: add merchant data per system.
- travel.py: pirate encounter triggers combat.
- gui/app.py: new panels — Combat Log, Tech Tree, Rumors, Reputation, Merchant Chat.

## 7. LLM Integration (optional layer)
- Ollama endpoint :11434 for dialogue generation
- Prompt: "You are a {personality} merchant in a space trading game. The player has reputation {rep} with your faction. They want to {action}. Respond in 1-2 sentences."
- Cache responses by (merchant_id, player_rep, action) to avoid redundant calls
- If LLM fails, fall back to deterministic template responses
