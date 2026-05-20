"""
Caravaneer to the Stars — NiceGUI Web Interface

A comprehensive, polished GUI for the Caravaneer space trading game.
Dark space-themed aesthetic with deep navy/black backgrounds,
cyan/amber accents, and crisp typography.

Uses NiceGUI for the UI layer and HTML5 Canvas for hex map rendering.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from dataclasses import dataclass, field
from typing import Optional

from nicegui import app, ui
from nicegui.events import MouseEventArguments

from caravaneer import (
    Game, GameConfig, TurnLog,
    Position, TerrainType, SystemClass, Faction,
    DiscoveryType, EventType, VictoryType, GoodCategory,
    TradeGood, TRADE_GOODS, ShipClass, ShipTemplate, SHIP_TEMPLATES,
    ModuleSlot, ModuleTemplate, MODULE_TEMPLATES, HexDir,
    GalaxyMap, SystemData, TerrainCell,
    Ship, Player, CargoItem, ShipModule,
    TravelEngine, TravelResult, TravelOption, TravelEvent,
    Economy, TradeResult, MarketEvent,
)
from caravaneer.combat import CombatResult
from caravaneer.ai_agent import AIPersonality
from caravaneer.tech_tree import TechProgress, Technology
from caravaneer.rumors import Rumor, Merchant

# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

TERRAIN_COLORS = {
    "EMPTY": "#0a0a1a",
    "STAR_SYSTEM": "#ffd700",
    "ASTEROID_FIELD": "#8b7355",
    "NEBULA": "#6a0dad",
    "BLACK_HOLE": "#220033",
    "WORMHOLE": "#00ffcc",
    "SPACE_STATION": "#44ff44",
    "PIRATE_LAIR": "#ff2222",
    "DERELICT": "#888888",
    "ANOMALY": "#ff69b4",
}

TERRAIN_LABELS = {
    "EMPTY": "Deep Space",
    "STAR_SYSTEM": "Star System",
    "ASTEROID_FIELD": "Asteroid Field",
    "NEBULA": "Nebula",
    "BLACK_HOLE": "Black Hole",
    "WORMHOLE": "Wormhole",
    "SPACE_STATION": "Station",
    "PIRATE_LAIR": "Pirate Lair",
    "DERELICT": "Derelict",
    "ANOMALY": "Anomaly",
}

TERRAIN_SYMBOLS = {
    "EMPTY": "",
    "STAR_SYSTEM": "★",
    "ASTEROID_FIELD": "⚡",
    "NEBULA": "~",
    "BLACK_HOLE": "◉",
    "WORMHOLE": "∞",
    "SPACE_STATION": "⚓",
    "PIRATE_LAIR": "☠",
    "DERELICT": "⚙",
    "ANOMALY": "?",
}

HEX_SIZE = 24  # pixels, flat-top hexagon

PROGRESS_COLORS = {
    "hull": "red",
    "shields": "blue",
    "fuel": "yellow",
    "cargo": "orange",
}

PERSONALITY_EMOJI = {
    AIPersonality.TRADER: "📈",
    AIPersonality.EXPLORER: "🔭",
    AIPersonality.PIRATE: "☠️",
    AIPersonality.SMUGGLER: "👁️",
    AIPersonality.MERCENARY: "⚔️",
}

PERSONALITY_COLOR = {
    AIPersonality.TRADER: "text-green-400",
    AIPersonality.EXPLORER: "text-purple-400",
    AIPersonality.PIRATE: "text-red-400",
    AIPersonality.SMUGGLER: "text-orange-400",
    AIPersonality.MERCENARY: "text-yellow-400",
}

# ═══════════════════════════════════════════════════════════════════════════════
# Game Session
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GameSession:
    """Per-browser-session game state."""
    game: Game
    player_id: str
    player_name: str
    selected_position: Optional[Position] = None
    travel_options: list[TravelOption] = field(default_factory=list)
    log_messages: list[str] = field(default_factory=list)
    active_tab: str = "nav"

    @property
    def ship(self) -> Optional[Ship]:
        player = self.game.players.get(self.player_id)
        return player.primary_ship if player else None

    @property
    def player(self) -> Optional[Player]:
        return self.game.players.get(self.player_id)


# Global game sessions (keyed by NiceGUI client ID)
game_sessions: dict[int, GameSession] = {}


def get_session() -> Optional[GameSession]:
    """Get the game session for the current NiceGUI client."""
    client = ui.context.client
    if client is None:
        return None
    return game_sessions.get(client.id)


def create_new_game(name: str, ship_template: str, seed: int, radius: int, npcs: int) -> GameSession:
    """Create and return a new GameSession."""
    cfg = GameConfig(
        seed=seed,
        galaxy_radius=radius,
        npc_count=npcs,
        enable_combat=True,
        enable_tech_trees=True,
        enable_rumors=True,
    )
    game = Game(cfg)
    player_id = "p1"
    game.add_player(player_id, name, ship_template)

    # Spawn NPCs
    for i in range(npcs):
        personalities = [
            AIPersonality.TRADER, AIPersonality.EXPLORER, AIPersonality.PIRATE,
            AIPersonality.SMUGGLER, AIPersonality.MERCENARY,
        ]
        personality = personalities[i % len(personalities)]
        try:
            game.add_npc_trader(personality=personality.lower())
        except ValueError:
            break  # No more room

    session = GameSession(
        game=game,
        player_id=player_id,
        player_name=name,
    )
    session.travel_options = game.get_travel_options(player_id)
    return session


# ═══════════════════════════════════════════════════════════════════════════════
# Hex Map Renderer (HTML5 Canvas)
# ═══════════════════════════════════════════════════════════════════════════════

def hex_to_pixel(q: int, r: int) -> tuple[float, float]:
    """Convert axial hex coordinates to pixel position (flat-top)."""
    x = HEX_SIZE * (3.0 / 2.0 * q)
    y = HEX_SIZE * (math.sqrt(3) / 2.0 * q + math.sqrt(3) * r)
    return x, y


def build_hex_map_js(session: GameSession) -> str:
    """Push fresh hex data to the browser and trigger a redraw."""
    game = session.game
    galaxy = game.galaxy
    ship = session.ship
    if not ship:
        return ""

    visible = galaxy.visible_cells(session.player_id)
    ship_pos = ship.position
    ship_px, ship_py = hex_to_pixel(ship_pos.q, ship_pos.r)

    cells_data = []
    for pos, cell in galaxy.cells.items():
        if pos not in visible:
            continue
        px, py = hex_to_pixel(pos.q, pos.r)
        terrain_name = cell.terrain.name if hasattr(cell.terrain, "name") else str(cell.terrain)
        color = TERRAIN_COLORS.get(terrain_name, "#333333")
        label = ""
        if cell.system:
            label = cell.system.name
        elif terrain_name == "WORMHOLE":
            label = "Wormhole"
        elif terrain_name == "PIRATE_LAIR":
            label = "Pirates"
        elif terrain_name == "ANOMALY":
            label = "Anomaly"

        hazard_flags = []
        if cell.terrain == TerrainType.ASTEROID_FIELD:
            hazard_flags.append("asteroid")
        elif cell.terrain == TerrainType.NEBULA:
            hazard_flags.append("nebula")

        cells_data.append({
            "q": pos.q, "r": pos.r,
            "x": round(px, 1), "y": round(py, 1),
            "color": color,
            "label": label,
            "isShip": pos == ship_pos,
            "terrain": terrain_name,
            "hazards": hazard_flags,
        })

    travel_data = []
    for opt in session.travel_options:
        tpx, tpy = hex_to_pixel(opt.destination.q, opt.destination.r)
        travel_data.append({
            "q": opt.destination.q, "r": opt.destination.r,
            "x": round(tpx, 1), "y": round(tpy, 1),
            "systemName": opt.system_name or "",
            "fuelCost": opt.fuel_cost,
            "distance": opt.distance,
        })

    data_json = json.dumps({
        "cells": cells_data,
        "travel": travel_data,
        "shipPX": ship_px,
        "shipPY": ship_py,
        "shipQ": ship_pos.q,
        "shipR": ship_pos.r,
        "hexSize": HEX_SIZE,
    })

    return f"""(function(){{
    window._hexMapData = {data_json};
    if (window.hexMapDraw) window.hexMapDraw();
}})();"""

# ═══════════════════════════════════════════════════════════════════════════════
# UI Builders
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_bar(current: float, maximum: float, color: str) -> str:
    """Return NiceGUI linear-progress HTML string for a stat bar."""
    ratio = max(0.0, min(1.0, current / maximum)) if maximum > 0 else 0.0
    return f'<q-linear-progress value="{ratio}" color="{color}" track-color="grey-9" class="q-mt-xs" style="height:6px;border-radius:3px;"></q-linear-progress>'


def _add_log(session: GameSession, message: str):
    """Append a timestamped message to the session log."""
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    session.log_messages.append(f"[{ts}] {message}")
    # Keep last 20
    session.log_messages = session.log_messages[-20:]


def create_new_game_dialog():
    """Show a dialog to configure and start a new game."""
    with ui.dialog() as dialog, ui.card().classes("bg-gray-900 text-white w-96"):
        ui.label("New Game").classes("text-xl font-bold text-amber-400 mb-1")
        ui.label("Caravaneer to the Stars").classes("text-sm text-gray-400 mb-4")

        name_input = ui.input("Captain Name", value="Captain Zara").classes("w-full mb-2 text-white")
        ship_select = ui.select(
            label="Ship Type",
            options=list(SHIP_TEMPLATES.keys()),
            value="freighter",
        ).classes("w-full mb-2 text-white")

        seed_input = ui.number("Map Seed (0=random)", value=0, min=0, max=999999).classes("w-full mb-2 text-white")
        radius_slider = ui.slider(min=8, max=25, value=15, step=1).classes("w-full mb-1")
        ui.label().bind_text_from(radius_slider, "value", lambda v: f"Galaxy Radius: {v}").classes("text-sm text-gray-400 mb-4")

        npc_slider = ui.slider(min=0, max=8, value=3, step=1).classes("w-full mb-1")
        ui.label().bind_text_from(npc_slider, "value", lambda v: f"NPCs: {v}").classes("text-sm text-gray-400 mb-4")

        async def start_game():
            try:
                dialog.close()
                seed = int(seed_input.value) if seed_input.value else random.randint(1, 999999)
                session = create_new_game(
                    name=name_input.value or "Captain",
                    ship_template=ship_select.value or "freighter",
                    seed=seed,
                    radius=int(radius_slider.value),
                    npcs=int(npc_slider.value),
                )
                client = ui.context.client
                game_sessions[client.id] = session
                ui.notify(f"Game started! Seed: {seed}", type="positive", color="primary")
                await rebuild_ui(session)
            except Exception as exc:
                ui.notify(f"Launch failed: {exc}", type="negative")
                import traceback
                traceback.print_exc()

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat color=grey")
            ui.button("Launch!", on_click=start_game).props("color=primary")

    dialog.open()


async def rebuild_ui(session: GameSession):
    """Rebuild the entire UI after game state changes."""
    try:
        ui.context.client.content.clear()
    except Exception as e:
        ui.notify(f"Clear error: {e}", type="negative")
    try:
        with ui.context.client.content:
            await build_game_ui(session)
    except Exception as e:
        ui.notify(f"UI build error: {e}", type="negative")
        import traceback
        traceback.print_exc()


async def build_game_ui(session: GameSession):
    """Build the main game UI."""
    game = session.game
    ship = session.ship
    player = session.player

    has_combat = False
    if game.turn_logs:
        has_combat = bool(game.turn_logs[-1].combat_results)

    at_system = False
    cell = game.galaxy.get_cell(ship.position) if ship else None
    if cell and cell.system:
        at_system = True

    # Main outer container
    with ui.element("div").classes("w-full h-screen flex flex-col bg-gray-950 overflow-hidden") as container:

        # ── Title Bar ──────────────────────────────────────────────────────
        with ui.row().classes("w-full items-center gap-4 px-4 py-2 bg-gray-900 border-b border-gray-800 shrink-0"):
            ui.label("🚀 Caravaneer to the Stars").classes("text-lg font-bold text-amber-400")
            ui.space()
            if ship:
                ui.label(f"💰 {ship.credits:,} cr").classes("text-sm font-bold text-amber-300")
            ui.label(f"Turn {game.turn_number}").classes("text-sm text-gray-400")
            ui.button("New Game", on_click=create_new_game_dialog).props("flat size=sm color=grey")
            async def on_end_turn():
                await process_turn(session)
            ui.button("⏩ End Turn", on_click=on_end_turn).classes("ml-2").props("color=amber size=sm")

        # ── Game Over Banner ─────────────────────────────────────────────
        if game.game_over:
            winner_name = game.winner.name if game.winner else "?"
            with ui.element("div").classes("w-full bg-green-900 border-b border-green-700 py-2 px-4 text-center shrink-0"):
                ui.label(f"🏆 GAME OVER — {winner_name} wins! {game.victory_reason}").classes("text-sm font-bold text-green-300")

        # ── Main Content (tabbed viewer + ship panel) ─────────────────────
        with ui.row().classes("w-full flex-1 gap-0 overflow-hidden"):

            # ── Center-Left: Tabbed Main Viewer ──────────────────────────
            with ui.column().classes("flex-1 min-w-0 relative"):
                with ui.tabs().classes("w-full bg-gray-800 text-white") as tabs:
                    system_tab = ui.tab("📍 System", icon="place")
                    market_tab = ui.tab("💰 Market", icon="storefront")
                    nav_tab = ui.tab("🧭 Nav", icon="navigation")
                    intel_tab = ui.tab("👁️ Intel", icon="visibility")
                    tech_tab = ui.tab("🔬 Tech", icon="science")
                    log_tab = ui.tab("📡 Log", icon="chat")

                tab_value = nav_tab if session.active_tab == "nav" else system_tab if session.active_tab == "system" else market_tab if session.active_tab == "market" else intel_tab if session.active_tab == "intel" else tech_tab if session.active_tab == "tech" else log_tab
                with ui.tab_panels(tabs, value=tab_value).classes("w-full flex-1 bg-gray-900 text-white overflow-auto"):

                    # ── Nav Tab (Map + Destinations) ───────────────
                    with ui.tab_panel(nav_tab).classes("h-full p-0"):
                        with ui.row().classes("w-full h-full gap-0"):
                            # Canvas area
                            with ui.column().classes("flex-1 h-full relative").style("min-height:300px;"):
                                canvas_html = ui.html('''<canvas id="hexcanvas" style="width:100%;height:100%;display:block;cursor:crosshair;"></canvas>''').classes("w-full h-full")
                                canvas_html.on("click", lambda e: handle_map_click(e, session))

                            # Destination list panel
                            with ui.column().classes("w-56 h-full overflow-auto bg-gray-900 border-l border-gray-800 shrink-0 q-pa-sm"):
                                if ship:
                                    ui.label(f"📍 [{ship.position.q},{ship.position.r}]").classes("text-xs text-gray-500 mb-2")
                                if session.travel_options:
                                    ui.label("Destinations").classes("text-sm font-bold text-cyan-300 mb-2")
                                    for i, opt in enumerate(session.travel_options, 1):
                                        tname = opt.system_name or TERRAIN_LABELS.get(opt.terrain.name if hasattr(opt.terrain, "name") else str(opt.terrain), "Deep Space")
                                        hazard_text = " | ".join(opt.hazards) if opt.hazards else ""
                                        with ui.row().classes("w-full items-center gap-1 text-sm py-1 border-b border-gray-700"):
                                            with ui.column().classes("flex-1 min-w-0"):
                                                ui.label(f"{i}. {tname}").classes("truncate text-gray-200")
                                                if hazard_text:
                                                    ui.label(hazard_text).classes("text-red-400 text-2xs")
                                            with ui.column().classes("items-end shrink-0"):
                                                with ui.row().classes("items-center gap-1"):
                                                    ui.label(f"{opt.distance}j").classes("text-gray-500 text-xs")
                                                    ui.label(f"{opt.fuel_cost:.0f}⚡").classes("text-gray-500 text-xs")
                                                ui.button("Go", on_click=lambda _, o=opt: travel_to(session, o)).props("size=xs flat color=primary")
                                else:
                                    ui.label("No travel options available.").classes("text-sm text-gray-500")

                    # ── System Tab ─────────────────────────────────────
                    with ui.tab_panel(system_tab):
                        if cell and cell.system:
                            with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                                ui.label(f"{cell.system.name}").classes("text-sm font-bold text-cyan-300 mb-1")
                                ui.label(f"{cell.system.system_class.name} — {cell.system.faction.name}").classes("text-sm text-gray-400")
                                if cell.system.description:
                                    ui.label(cell.system.description).classes("text-sm text-gray-500 mt-1")

                                # Merchant
                                merchant = game.get_merchant(session.player_id)
                                if merchant:
                                    with ui.element("div").classes("w-full bg-gray-700 rounded q-pa-xs q-mt-sm"):
                                        ui.label(f"🛒 Merchant: {merchant.name}").classes("text-sm font-bold text-amber-300")
                                        ui.label(f"Personality: {merchant.personality.title()}").classes("text-sm text-gray-400")
                                        ui.label(merchant.greet(player) if player else "...").classes("text-sm text-gray-200 italic mt-1")
                                        ui.label(f"Buy markup: {merchant.buy_markup:.2f}x | Sell discount: {merchant.sell_discount:.2f}x").classes("text-sm text-gray-500")

                                # Services
                                rep = player.get_reputation(cell.system.faction) if player else 0
                                service_blocked = rep < -50
                                with ui.row().classes("w-full gap-2 mt-2"):
                                    async def do_refuel():
                                        msg = game.player_refuel(session.player_id)
                                        _add_log(session, f"Refuel: {msg}")
                                        session.active_tab = "system"
                                        await rebuild_ui(session)
                                    async def do_repair():
                                        msg = game.player_repair(session.player_id)
                                        _add_log(session, f"Repair: {msg}")
                                        session.active_tab = "system"
                                        await rebuild_ui(session)
                                    ui.button("Refuel", on_click=do_refuel).props("size=sm flat color=yellow").classes("" if not service_blocked else "disabled")
                                    ui.button("Repair", on_click=do_repair).props("size=sm flat color=red").classes("" if not service_blocked else "disabled")
                                if service_blocked:
                                    ui.label("Service refused — reputation too low!").classes("text-sm text-red-400 mt-1")
                        else:
                            ui.label("Deep space — no system here.").classes("text-sm text-gray-500 q-pa-md")

                    # ── Market Tab ─────────────────────────────────────
                    with ui.tab_panel(market_tab).classes("h-full flex flex-col"):
                        cell_sys = cell.system if cell else None
                        market = game.get_market(session.player_id) if cell_sys else None
                        with ui.column().classes("w-full flex-1 overflow-auto"):
                            # — Cargo (top of tab so Sell is visible) —
                            if ship and ship.cargo_hold:
                                ui.label("📦 Your Cargo — Sell").classes("text-sm font-bold text-cyan-300 q-mt-sm q-mb-xs")
                                for item in ship.cargo_hold:
                                    current_price = 0
                                    cell_local = game.galaxy.get_cell(ship.position)
                                    if cell_local and cell_local.system:
                                        mkt = game.economy.market_summary(cell_local.system)
                                        if item.good_key in mkt:
                                            current_price = mkt[item.good_key]["price"]
                                    total_paid = item.purchase_price * item.quantity
                                    profit = (current_price - item.purchase_price) * item.quantity
                                    profit_color = "text-green-400" if profit >= 0 else "text-red-400"
                                    profit_sign = "+" if profit >= 0 else ""
                                    with ui.row().classes("w-full items-center gap-1 text-sm py-1 border-b border-gray-700"):
                                        with ui.column().classes("flex-1 min-w-0"):
                                            ui.label(f"{item.good.name}").classes("text-gray-200")
                                            ui.label(f"Qty {item.quantity}  ·  Wt {item.total_weight:.1f}  ·  Unit {item.purchase_price}cr  ·  Total {total_paid}cr").classes("text-gray-500 text-xs")
                                        with ui.column().classes("items-end"):
                                            if current_price:
                                                ui.label(f"Value {current_price}cr").classes("text-gray-400")
                                                ui.label(f"{profit_sign}{profit}cr").classes(f"{profit_color} text-xs")
                                            qty_sel = ui.select([1, 5, 10, 25, 50, "all"], value=1, label="Qty").props("dense options-dense").classes("text-sm w-20")
                                            ui.button("Sell", on_click=lambda _, gk=item.good_key, mx=item.quantity, qs=qty_sel: _do_sell(session, gk, mx, qs)).props("size=sm flat color=orange")
                            elif ship and not ship.cargo_hold:
                                ui.label("📦 Cargo hold empty.").classes("text-sm text-gray-500 q-mt-sm")

                            ui.separator().classes("q-my-md")

                            # — Market Goods —
                            if cell_sys and market:
                                ui.label("🛒 Market — Buy").classes("text-sm font-bold text-amber-300 q-mt-sm q-mb-xs")
                                for good_key, info in market.items():
                                    trend_icon = "▲" if info["trend"] == "up" else "▼" if info["trend"] == "down" else "→"
                                    trend_color = "text-red-400" if info["trend"] == "up" else "text-green-400" if info["trend"] == "down" else "text-gray-400"
                                    illegal_text = "⚠️ Illegal" if info["illegal"] else ""
                                    with ui.row().classes("w-full items-center gap-1 text-sm py-1 border-b border-gray-700"):
                                        with ui.column().classes("flex-1 min-w-0"):
                                            with ui.row().classes("items-center gap-1"):
                                                ui.label(f"{info['name']}").classes("text-gray-200")
                                                ui.label(illegal_text).classes("text-red-500 text-xs")
                                            ui.label(f"Base {info['base_price']}cr  ·  {info['category']}").classes("text-gray-500 text-xs")
                                        with ui.column().classes("items-end"):
                                            with ui.row().classes("items-center gap-1"):
                                                ui.label(f"{info['price']}cr").classes(trend_color)
                                                ui.label(trend_icon).classes(trend_color)
                                            qty_select = ui.select([1, 5, 10, 25, 50, 100], value=1, label="Qty").props("dense options-dense").classes("text-sm w-20")
                                            ui.button("Buy", on_click=lambda _, gk=good_key, qs=qty_select: _do_buy(session, gk, qs)).props("size=sm flat color=green")
                            elif cell_sys:
                                ui.label("No market available.").classes("text-sm text-gray-500")
                            else:
                                ui.label("No market in deep space.").classes("text-sm text-gray-500 q-pa-md")
                    # ── Intel Tab ──────────────────────────────────────
                    with ui.tab_panel(intel_tab):
                        # NPC Roster
                        ui.label("🤖 NPC Roster").classes("text-sm font-bold text-gray-300 q-mt-sm q-mb-xs")
                        with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                            state = game.get_game_state(session.player_id)
                            npcs = state.get("npcs", [])
                            if not npcs:
                                ui.label("No NPCs in the galaxy.").classes("text-sm text-gray-500")
                            else:
                                for npc in npcs:
                                    personality = npc.get("personality") or "trader"
                                    emoji = PERSONALITY_EMOJI.get(personality, "🤖")
                                    color = PERSONALITY_COLOR.get(personality, "text-gray-400")
                                    same_pos = ship and npc.get("position") == str(ship.position)
                                    with ui.row().classes("w-full items-center gap-1 text-sm py-1 border-b border-gray-700"):
                                        with ui.column().classes("flex-1 min-w-0"):
                                            with ui.row().classes("items-center gap-1"):
                                                ui.label(emoji).classes("text-sm")
                                                ui.label(f"{npc['name']}").classes(f"font-bold {color}")
                                            ui.label(f"Ship: {npc.get('ship', '?')} | Pos: {npc.get('position', '?')}").classes("text-gray-500 text-xs")
                                        if same_pos and npc.get("id"):
                                            async def attack_npc():
                                                result = game.player_attack(session.player_id, npc.get("id"))
                                                _add_log(session, f"Attack: {result.message}")
                                                for msg in result.log:
                                                    _add_log(session, f"  → {msg}")
                                                session.active_tab = "intel"
                                                await rebuild_ui(session)
                                            ui.button("Attack", on_click=attack_npc).props("size=sm flat color=red")

                        # Reputation
                        ui.label("🛡️ Reputation").classes("text-sm font-bold text-gray-300 q-mt-md q-mb-xs")
                        with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                            if player:
                                for faction, rep in player.reputation.items():
                                    if rep > 0:
                                        badge_color = "bg-green-800 text-green-300"
                                    elif rep < 0:
                                        badge_color = "bg-red-800 text-red-300"
                                    else:
                                        badge_color = "bg-gray-700 text-gray-400"
                                    with ui.row().classes("w-full items-center gap-2 text-sm py-1"):
                                        ui.label(faction.name).classes("flex-1 text-gray-300")
                                        with ui.element("span").classes(f"px-2 py-1 rounded text-sm font-bold {badge_color}"):
                                            ui.label(f"{rep:+d}")
                            else:
                                ui.label("No player data.").classes("text-sm text-gray-500")

                        # Rumors
                        ui.label("📣 Rumors").classes("text-sm font-bold text-gray-300 q-mt-md q-mb-xs")
                        with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                            rumors = game.get_rumors(session.player_id)
                            if rumors:
                                for r in rumors:
                                    truth_color = "text-green-400" if r.truth_value >= 0.8 else "text-yellow-400" if r.truth_value >= 0.5 else "text-red-400"
                                    with ui.row().classes("w-full items-start gap-1 text-sm py-1 border-b border-gray-700"):
                                        with ui.column().classes("flex-1 min-w-0"):
                                            ui.label(r.text).classes("text-gray-200")
                                            ui.label(f"Category: {r.category} | Truth: {r.truth_value:.0%}").classes(f"{truth_color} text-xs")
                            else:
                                ui.label("No rumors in this sector.").classes("text-sm text-gray-500")

                    # ── Tech Tab ───────────────────────────────────────
                    with ui.tab_panel(tech_tab):
                        with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                            techs = game.get_available_techs(session.player_id)
                            progress = game.get_tech_progress(session.player_id)

                            if progress.unlocked:
                                ui.label("Unlocked Techs").classes("text-sm font-bold text-cyan-300 mb-1")
                                ui.label(f"Fuel eff: {progress.fuel_efficiency:.2f}x | Shield regen: +{progress.shield_regen} | Attack: +{progress.attack_bonus} | Sensors: +{progress.sensor_bonus} | Jump: +{progress.jump_bonus}").classes("text-sm text-gray-400 mb-2")
                                if progress.special:
                                    ui.label(f"Specials: {', '.join(progress.special)}").classes("text-sm text-purple-400 mb-2")
                            else:
                                ui.label("No techs unlocked yet.").classes("text-sm text-gray-500 mb-2")

                            if techs:
                                ui.label("Available Techs").classes("text-sm font-bold text-amber-300 mb-1")
                                by_faction: dict[str, list] = {}
                                for t in techs:
                                    by_faction.setdefault(t.faction.name, []).append(t)
                                for faction_name, faction_techs in by_faction.items():
                                    ui.label(faction_name).classes("text-sm font-bold text-gray-400 mt-1")
                                    for t in faction_techs:
                                        with ui.row().classes("w-full items-start gap-1 text-sm py-1 border-b border-gray-700"):
                                            with ui.column().classes("flex-1 min-w-0"):
                                                ui.label(f"{t.name} (Tier {t.tier})").classes("text-gray-200")
                                                ui.label(f"{t.branch.name} | Cost: {t.cost}cr | Rep req: {t.reputation_req}").classes("text-gray-500 text-xs")
                                                ui.label(t.description).classes("text-gray-400 text-xs")
                                            async def unlock_tech(tech_key=t.key):
                                                ok, msg = game.player_unlock_tech(session.player_id, tech_key)
                                                _add_log(session, f"Tech: {msg}")
                                                session.active_tab = "tech"
                                                await rebuild_ui(session)
                                            ui.button("Unlock", on_click=unlock_tech).props("size=sm flat color=primary")
                            else:
                                ui.label("No available techs (check reputation/credits).").classes("text-sm text-gray-500")

                    # ── Log Tab ────────────────────────────────────────
                    with ui.tab_panel(log_tab):
                        # Combat Log
                        ui.label("💥 Combat Log").classes("text-sm font-bold text-gray-300 q-mt-sm q-mb-xs")
                        with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                            if game.turn_logs and any(game.turn_logs[-1].combat_results):
                                for cr in game.turn_logs[-1].combat_results:
                                    outcome = ""
                                    if cr.defender_destroyed:
                                        outcome = "💀 Defender destroyed"
                                    elif cr.attacker_fled:
                                        outcome = "🏃 Attacker fled"
                                    elif cr.defender_fled:
                                        outcome = "🏃 Defender fled"
                                    else:
                                        outcome = "🤝 Standoff"
                                    ui.label(f"{cr.attacker.name} vs {cr.defender.name} — {outcome}").classes("text-sm font-bold text-red-300")
                                    ui.label(f"Turns: {cr.turns} | Dmg dealt: {cr.attacker_damage_dealt}/{cr.defender_damage_dealt} | Hits: {cr.hits_landed}").classes("text-sm text-gray-400")
                                    if cr.loot_credits:
                                        ui.label(f"Loot: {cr.loot_credits}cr").classes("text-sm text-green-400")
                                    if cr.message:
                                        ui.label(cr.message).classes("text-sm text-gray-500")
                                    ui.separator().classes("q-my-xs")
                            else:
                                ui.label("No combat this turn.").classes("text-sm text-gray-500")

                        # Communications Log
                        ui.label("📡 Communications Log").classes("text-sm font-bold text-gray-300 q-mt-md q-mb-xs")
                        with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                            if session.log_messages:
                                for msg in session.log_messages[-20:]:
                                    ui.label(msg).classes("text-sm text-gray-400 py-1")
                            else:
                                ui.label("No messages yet.").classes("text-sm text-gray-500")

            # ── Right: Ship Status Panel (always visible) ────────────────
            with ui.column().classes("w-72 bg-gray-900 border-l border-gray-800 shrink-0 q-pa-sm").style("height:100%"):
                if ship:
                    with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                        ui.label(f"{ship.name}").classes("text-sm font-bold text-cyan-300 mb-1")
                        ui.label(f"Class: {ship.template.name}").classes("text-sm text-gray-400 mb-2")

                        ui.label(f"Hull: {ship.hull:.0f} / {ship.max_hull}").classes("text-sm text-gray-300")
                        ui.linear_progress(value=ship.hull / ship.max_hull, show_value=False, color="red").classes("q-mt-xs").style("height:6px;border-radius:3px;")
                        ui.label(f"Shields: {ship.shields:.0f} / {ship.max_shields}").classes("text-sm text-gray-300 q-mt-sm")
                        ui.linear_progress(value=ship.shields / ship.max_shields, show_value=False, color="blue").classes("q-mt-xs").style("height:6px;border-radius:3px;")
                        ui.label(f"Fuel: {ship.fuel:.0f} / {ship.max_fuel}").classes("text-sm text-gray-300 q-mt-sm")
                        ui.linear_progress(value=ship.fuel / ship.max_fuel, show_value=False, color="yellow").classes("q-mt-xs").style("height:6px;border-radius:3px;")
                        cargo_ratio = ship.used_cargo / ship.max_cargo if ship.max_cargo > 0 else 0
                        ui.label(f"Cargo: {ship.used_cargo:.1f} / {ship.max_cargo}").classes("text-sm text-gray-300 q-mt-sm")
                        ui.linear_progress(value=cargo_ratio, show_value=False, color="orange").classes("q-mt-xs").style("height:6px;border-radius:3px;")

                        with ui.grid(columns=2).classes("w-full gap-1 text-sm q-mt-sm"):
                            ui.label("Jump Range:").classes("text-gray-400")
                            ui.label(f"{ship.jump_range}").classes("text-right text-cyan-300")
                            ui.label("Speed:").classes("text-gray-400")
                            ui.label(f"{ship.speed}").classes("text-right text-cyan-300")
                            ui.label("Sensors:").classes("text-gray-400")
                            ui.label(f"{ship.sensor_range}").classes("text-right text-cyan-300")
                            ui.label("Attack:").classes("text-gray-400")
                            ui.label(f"{ship.attack_power}").classes("text-right text-red-300")

                    # Armaments
                    weapons = [m for m in ship.modules if m.template.slot == ModuleSlot.WEAPON]
                    if weapons:
                        ui.label("⚔️ Armaments").classes("text-sm font-bold text-gray-300 q-mt-md q-mb-xs")
                        with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                            for w in weapons:
                                atk = int(w.template.effect_value)
                                ui.label(f"{w.template.name} (+{atk} atk)").classes("text-sm text-gray-300")
                            ui.separator().classes("q-my-xs")
                            ui.label(f"Total Attack: {ship.attack_power}").classes("text-sm font-bold text-red-400")

                    # Ship Modules
                    if ship.modules:
                        ui.label("🔧 Modules").classes("text-sm font-bold text-gray-300 q-mt-md q-mb-xs")
                        with ui.card().classes("w-full bg-gray-800 text-white q-pa-sm"):
                            slot_order = [ModuleSlot.ENGINE, ModuleSlot.SHIELD, ModuleSlot.WEAPON, ModuleSlot.SENSOR, ModuleSlot.CARGO, ModuleSlot.UTILITY]
                            for slot in slot_order:
                                mods = [m for m in ship.modules if m.template.slot == slot]
                                if mods:
                                    ui.label(f"{slot.name}").classes("text-sm font-bold text-gray-400 mt-1")
                                    for m in mods:
                                        effect = f"(+{m.template.effect_value:.0f})" if m.template.effect_value else ""
                                        ui.label(f"  {m.template.name} {effect}").classes("text-sm text-gray-300")

                else:
                    ui.label("No ship data.").classes("text-sm text-gray-500 q-pa-md")

    # Ensure canvas is rendered if we're on the Nav tab
    if session.active_tab == "nav":
        update_canvas(session)


def update_canvas(session: GameSession):
    """Update the hex map canvas via JavaScript."""
    js = build_hex_map_js(session)
    if js:
        ui.run_javascript(js)


# ═══════════════════════════════════════════════════════════════════════════════
# Interaction Handlers
# ═══════════════════════════════════════════════════════════════════════════════

async def travel_to(session: GameSession, option: TravelOption):
    """Travel to a destination."""
    game = session.game
    result = game.player_travel(session.player_id, option.destination)
    _add_log(session, f"Travel: {result.message}")
    for evt in result.events:
        _add_log(session, f"  → {evt.description}")
    session.travel_options = game.get_travel_options(session.player_id)
    await rebuild_ui(session)


async def buy_good(session: GameSession, good_key: str, quantity):
    """Buy a good from the market."""
    game = session.game
    qty = int(quantity) if quantity and int(quantity) > 0 else 1
    result = game.player_buy(session.player_id, good_key, qty)
    _add_log(session, f"Buy: {result.message}")
    session.travel_options = game.get_travel_options(session.player_id)
    session.active_tab = "market"
    await rebuild_ui(session)


async def sell_good(session: GameSession, good_key: str, quantity):
    """Sell a good from cargo."""
    game = session.game
    qty = quantity
    if qty == "all":
        ship = session.ship
        qty = 0
        if ship:
            for item in ship.cargo_hold:
                if item.good_key == good_key:
                    qty = item.quantity
                    break
    qty = int(qty) if qty and int(qty) > 0 else 1
    result = game.player_sell(session.player_id, good_key, qty)
    _add_log(session, f"Sell: {result.message}")
    session.travel_options = game.get_travel_options(session.player_id)
    session.active_tab = "market"
    await rebuild_ui(session)


async def process_turn(session: GameSession):
    """Process a turn."""
    game = session.game
    log = game.process_turn(session.player_id)
    _add_log(session, f"--- Turn {log.turn_number} ---")
    for action in log.actions:
        _add_log(session, f"  {action}")
    for evt in log.events:
        _add_log(session, f"  Event: {evt}")
    for tr in log.travel_results:
        _add_log(session, f"  Travel: {tr.message}")
    for trade in log.trade_results:
        _add_log(session, f"  Trade: {trade.message}")
    for disc in log.discoveries:
        _add_log(session, f"  Discovery: {disc}")
    for rumor in log.rumors:
        _add_log(session, f"  Rumor: {rumor}")
    for cr in log.combat_results:
        _add_log(session, f"  Combat: {cr.message}")
        for msg in cr.log:
            _add_log(session, f"    → {msg}")
    session.travel_options = game.get_travel_options(session.player_id)
    await rebuild_ui(session)


async def handle_map_click(e, session: GameSession):
    """Handle a click on the hex map canvas to travel."""
    if not session or not session.ship:
        return
    if not session.travel_options:
        ui.notify("No travel destinations available.", type="warning")
        return
    # Read the intended destination computed by the JS click handler
    result = await ui.run_javascript(
        "document.getElementById('hexcanvas')._travelTarget || null",
        timeout=2.0,
    )
    if result is None:
        ui.notify("Click a reachable destination (amber outline) to travel.", type="info")
        return
    target_q = result.get("q")
    target_r = result.get("r")
    for opt in session.travel_options:
        if opt.destination.q == target_q and opt.destination.r == target_r:
            await travel_to(session, opt)
            return
    ui.notify("That hex is not reachable. Click an amber-outlined destination.", type="warning")


async def handle_keyboard(e, session: GameSession):
    """Handle global keyboard shortcuts."""
    if e.key.name == "space" and e.action == "keydown":
        await process_turn(session)


# ── Buy / Sell helpers (called via lambda to avoid closure traps) ──

async def _do_buy(session: GameSession, good_key: str, qty_select):
    """Buy a quantity of a good. qty_select is the NiceGUI select element."""
    val = qty_select.value
    qty = int(val) if val is not None else 1
    if qty <= 0:
        ui.notify("Select a quantity greater than 0", type="warning")
        return
    await buy_good(session, good_key, qty)


async def _do_sell(session: GameSession, good_key: str, max_qty: int, qty_select):
    """Sell a quantity of a good. qty_select is the NiceGUI select element."""
    val = qty_select.value
    if val == "all":
        qty = max_qty
    else:
        qty = int(val) if val is not None else 1
    if qty <= 0:
        ui.notify("Select a quantity greater than 0", type="warning")
        return
    if qty > max_qty:
        qty = max_qty
    await sell_good(session, good_key, qty)


# ═══════════════════════════════════════════════════════════════════════════════
# Main Page
# ═══════════════════════════════════════════════════════════════════════════════

@ui.page("/")
async def index():
    """Main game page."""
    ui.add_head_html("""
        <style>
            body { background: #050510; margin: 0; overflow: hidden; }
            .nicegui-content { padding: 0 !important; }
            .text-sm { font-size: 0.8rem; line-height: 1rem; }
            .text-2xs { font-size: 0.7rem; line-height: 0.85rem; }
            /* NiceGUI select dropdown text color fix */
            .q-select .q-field__native, .q-select .q-field__prefix, .q-select .q-field__suffix,
            .q-select .q-field__input { color: #ffffff !important; }
            .q-menu .q-item { color: #ffffff !important; background: #1f2937 !important; }
            .q-menu .q-item.q-item--active { background: #374151 !important; }
            .q-menu { background: #1f2937 !important; border: 1px solid #374151; }
        </style>
        <script>
        (function() {
            // --- Permanent Hex Map Drawing Engine ---
            function hexMapDraw() {
                var canvas = document.getElementById('hexcanvas');
                if (!canvas) return;
                var dpr = window.devicePixelRatio || 1;
                var ctx = canvas.getContext('2d');
                var data = window._hexMapData;
                if (!data) return;

                var w = canvas.clientWidth || 800;
                var h = canvas.clientHeight || 400;
                if (w < 5 || h < 5) return;
                canvas.width = w * dpr;
                canvas.height = h * dpr;
                ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

                var shipPX = data.shipPX;
                var shipPY = data.shipPY;
                var shipQ = data.shipQ;
                var shipR = data.shipR;
                var hexSize = data.hexSize;
                var offsetX = w/2 - shipPX;
                var offsetY = h/2 - shipPY;
                data.offsetX = offsetX;
                data.offsetY = offsetY;

                // Background
                ctx.fillStyle = '#02020a';
                ctx.fillRect(0, 0, w, h);

                // Stars
                ctx.fillStyle = '#ffffff';
                for (var s = 0; s < 120; s++) {
                    var sx = (s * 137.5) % w;
                    var sy = (s * 293.3) % h;
                    var br = (s % 3 === 0) ? 1.2 : 0.6;
                    ctx.globalAlpha = (s % 7 === 0) ? 0.8 : 0.3;
                    ctx.beginPath();
                    ctx.arc(sx, sy, br, 0, Math.PI*2);
                    ctx.fill();
                }
                ctx.globalAlpha = 1.0;

                var sqrt3 = Math.sqrt(3);
                var hexW = hexSize * 3/2;
                var hexH = hexSize * sqrt3;
                var cols = Math.ceil(w / hexW) + 6;
                var rows = Math.ceil(h / hexH) + 6;
                var startQ = Math.floor((shipQ * hexW - w/2) / hexW) - 3;
                var startR = Math.floor((shipR * sqrt3 * hexSize - h/2) / hexH) - 3;

                // Grid outline
                ctx.strokeStyle = 'rgba(100,140,180,0.18)';
                ctx.lineWidth = 0.5;
                for (var rq = startQ; rq < startQ + cols; rq++) {
                    for (var rr = startR; rr < startR + rows; rr++) {
                        var fx = shipPX + (rq - shipQ) * hexW + offsetX;
                        var fy = shipPY + ((rr - shipR) * hexH + (rq - shipQ) * hexH/2) + offsetY;
                        if (fx < -hexSize*2 || fx > w + hexSize*2 || fy < -hexSize*2 || fy > h + hexSize*2) continue;
                        ctx.beginPath();
                        for (var i = 0; i < 6; i++) {
                            var angle = Math.PI/180 * (60 * i);
                            ctx.lineTo(fx + hexSize * Math.cos(angle), fy + hexSize * Math.sin(angle));
                        }
                        ctx.closePath();
                        ctx.stroke();
                    }
                }

                // Reachable destinations — amber stroke only
                var reachIdx = {};
                ctx.lineWidth = 1.0;
                ctx.strokeStyle = 'rgba(255, 180, 0, 0.75)';
                data.travel.forEach(function(t, ti) {
                    var cx = t.x + offsetX;
                    var cy = t.y + offsetY;
                    if (cx < -hexSize*2 || cx > w + hexSize*2 || cy < -hexSize*2 || cy > h + hexSize*2) return;
                    reachIdx[t.q + ':' + t.r] = ti;
                    ctx.beginPath();
                    for (var i = 0; i < 6; i++) {
                        var angle = Math.PI/180 * (60 * i);
                        ctx.lineTo(cx + hexSize * Math.cos(angle), cy + hexSize * Math.sin(angle));
                    }
                    ctx.closePath();
                    ctx.stroke();
                });
                canvas._reachIdx = reachIdx;

                // Visible cells
                ctx.lineWidth = 0.8;
                data.cells.forEach(function(c) {
                    var cx = c.x + offsetX;
                    var cy = c.y + offsetY;
                    if (cx < -hexSize*2.5 || cx > w + hexSize*2.5 || cy < -hexSize*2.5 || cy > h + hexSize*2.5) return;
                    ctx.beginPath();
                    for (var i = 0; i < 6; i++) {
                        var angle = Math.PI/180 * (60 * i);
                        ctx.lineTo(cx + hexSize * Math.cos(angle), cy + hexSize * Math.sin(angle));
                    }
                    ctx.closePath();
                    ctx.fillStyle = c.color;
                    ctx.fill();
                    if (c.hazards.indexOf('nebula') !== -1) {
                        ctx.fillStyle = 'rgba(106,13,173,0.25)';
                        ctx.fill();
                    }
                    if (c.hazards.indexOf('asteroid') !== -1) {
                        ctx.fillStyle = 'rgba(139,115,85,0.3)';
                        ctx.fill();
                        ctx.fillStyle = 'rgba(180,160,130,0.5)';
                        for (var a = 0; a < 5; a++) ctx.fillRect(cx + (a*53 % 14) - 7, cy + (a*91 % 14) - 7, 1.5, 1.5);
                    }
                    ctx.strokeStyle = 'rgba(255,255,255,0.35)';
                    ctx.stroke();
                    if (c.label) {
                        ctx.fillStyle = '#ffffff';
                        ctx.font = (c.isShip ? 'bold ' : '') + '10px sans-serif';
                        ctx.textAlign = 'center';
                        ctx.textBaseline = 'middle';
                        var name = c.label.length > 12 ? c.label.substring(0,11)+'\u2026' : c.label;
                        ctx.fillText(name, cx, cy - 2);
                    }
                });

                // Ship marker
                var sx = shipPX + offsetX;
                var sy = shipPY + offsetY;
                ctx.beginPath();
                ctx.moveTo(sx, sy - hexSize*0.45);
                ctx.lineTo(sx - hexSize*0.35, sy + hexSize*0.3);
                ctx.lineTo(sx + hexSize*0.35, sy + hexSize*0.3);
                ctx.closePath();
                ctx.fillStyle = '#00ff88';
                ctx.fill();
                ctx.strokeStyle = '#ccffee';
                ctx.lineWidth = 1.5;
                ctx.stroke();
            }
            window.hexMapDraw = hexMapDraw;

            // Canvas click handler (delegated, one-time install)
            document.addEventListener('click', function(ev) {
                var canvas = document.getElementById('hexcanvas');
                if (!canvas || ev.target !== canvas) return;
                var data = window._hexMapData;
                if (!data || !data.offsetX) return;
                var rect = canvas.getBoundingClientRect();
                var lx = ev.clientX - rect.left;
                var ly = ev.clientY - rect.top;
                var offsetX = data.offsetX;
                var offsetY = data.offsetY;
                var best = null;
                var bestDist = 999999;
                for (var ti=0; ti<data.travel.length; ti++) {
                    var t = data.travel[ti];
                    var tx = t.x + offsetX;
                    var ty = t.y + offsetY;
                    var dx = lx - tx;
                    var dy = ly - ty;
                    var dist = Math.sqrt(dx*dx + dy*dy);
                    if (dist < bestDist) { bestDist = dist; best = t; }
                }
                canvas._travelTarget = (best && bestDist < data.hexSize) ? best : null;
            });

            // Observe tab-panel visibility and redraw when Nav becomes visible
            var navTabPanel = null;
            function findNavPanel() {
                var panels = document.querySelectorAll('.q-tab-panel');
                for (var i=0; i<panels.length; i++) {
                    if (panels[i].textContent && panels[i].textContent.indexOf('Destinations') !== -1) {
                        navTabPanel = panels[i];
                        break;
                    }
                }
            }
            var mo = new MutationObserver(function(mutations) {
                if (!navTabPanel) findNavPanel();
                if (navTabPanel && !navTabPanel.classList.contains('q-tab-panel--inactive')) {
                    if (window.hexMapDraw) window.hexMapDraw();
                }
            });
            mo.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'] });
        })();
        </script>
    """)

    with ui.element("div").classes("w-full h-screen flex items-center justify-center bg-gray-950") as container:
        container.props("id=main_container")
        with ui.card().classes("bg-gray-900 text-white p-8 text-center"):
            ui.label("🚀").classes("text-6xl mb-4")
            ui.label("Caravaneer to the Stars").classes("text-3xl font-bold text-amber-400 mb-2")
            ui.label("A Space Trading & Exploration Game").classes("text-gray-400 mb-6")
            ui.button("New Game", on_click=create_new_game_dialog).classes("text-lg px-8 py-3").props("color=amber")


# ═══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def run(host: str = "127.0.0.1", port: int = 8500):
    """Launch the Caravaneer GUI server."""
    ui.run(
        host=host,
        port=port,
        title="Caravaneer to the Stars",
        favicon="🚀",
        reload=False,
        show=False,
    )
