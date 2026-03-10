from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .client import Sts2Client


def create_server(client: Sts2Client | None = None) -> FastMCP:
    sts2 = client or Sts2Client()
    mcp = FastMCP("STS2 AI Agent")

    @mcp.tool
    def health_check() -> dict[str, Any]:
        """Check whether the STS2 AI Agent Mod is loaded and the HTTP API is reachable.

        Always call this first to confirm the game is running and the mod is active.

        Returns:
            service: always "sts2-ai-agent"
            mod_version: mod semver
            protocol_version: wire protocol tag
            game_version: detected game build
            status: "ready" when operational

        Common errors:
            connection_error – game is not running or mod failed to load.
        """
        return sts2.get_health()

    @mcp.tool
    def get_game_state() -> dict[str, Any]:
        """Read a full snapshot of the current game state.

        Call this before making any decision — it tells you the current screen,
        available actions, combat details, map layout, run info, and more.

        Top-level fields:
            screen – current logical screen (MAIN_MENU, CHARACTER_SELECT, MAP,
                     COMBAT, EVENT, SHOP, REST, REWARD, CHEST, CARD_SELECTION,
                     GAME_OVER, UNKNOWN).
            in_combat – true while a fight is in progress.
            turn – round number during combat, null otherwise.
            available_actions – list of action names you may call right now.

        Conditional sections (present when relevant):
            combat.player – hp, max_hp, block, energy, stars.
            combat.hand[] – cards in hand with index, card_id, name, energy_cost,
                            playable flag, requires_target, unplayable_reason.
            combat.enemies[] – enemy index, hp, block, intent, is_alive.
            run – deck, relics, potions, gold, hp.
            map – current_node, available_nodes[].
            reward – pending_card_choice, rewards[], card_options[], alternatives[].
            selection – kind, prompt, cards[] (e.g. card removal screen).

        Common errors:
            state_unavailable – game is transitioning between screens; retry shortly.
        """
        return sts2.get_state()

    @mcp.tool
    def get_available_actions() -> list[dict[str, Any]]:
        """List actions that can be executed in the current game state.

        Each entry contains:
            name – action identifier to pass to an action tool.
            requires_target – whether target_index is needed.
            requires_index – whether card_index or option_index is needed.

        Use this as a quick check before calling action tools. For full context
        (e.g. which cards are playable), prefer get_game_state instead.
        """
        return sts2.get_available_actions()

    @mcp.tool
    def end_turn() -> dict[str, Any]:
        """End the player's turn during combat.

        Preconditions:
            - screen is COMBAT.
            - available_actions includes "end_turn".
            - It is the player's play phase.

        The mod waits for the turn transition to stabilize before responding.
        If stabilization times out, status will be "pending" instead of "completed".

        Returns updated game state in data.state after the turn ends.

        Common errors:
            invalid_action – not in combat or not your turn.
        """
        return sts2.end_turn()

    @mcp.tool
    def play_card(card_index: int, target_index: int | None = None) -> dict[str, Any]:
        """Play a card from the current hand.

        Args:
            card_index: zero-based index into combat.hand[].
            target_index: zero-based index into combat.enemies[]. Required only
                          when the card's requires_target is true (target_type
                          is AnyEnemy). Omit for AOE or self-targeting cards.

        Preconditions:
            - screen is COMBAT.
            - available_actions includes "play_card".
            - The card at card_index has playable=true.

        Decision flow:
            1. Read get_game_state() → check combat.hand[].
            2. Pick a card where playable=true.
            3. If requires_target=true, pick a target from combat.enemies[]
               where is_alive=true.
            4. Call play_card(card_index, target_index).

        Returns updated game state in data.state.

        Common errors:
            invalid_action – not in combat play phase, or no playable cards.
            invalid_request – card_index missing.
            invalid_target – card_index or target_index out of range.
        """
        return sts2.play_card(card_index=card_index, target_index=target_index)

    @mcp.tool
    def choose_map_node(option_index: int) -> dict[str, Any]:
        """Travel to a map node.

        Args:
            option_index: zero-based index into map.available_nodes[].

        Preconditions:
            - screen is MAP.
            - available_actions includes "choose_map_node".
            - map.available_nodes is non-empty.

        Each node has row, col, node_type (Enemy, Elite, Boss, Rest, Shop,
        Event, Treasure, etc.) and state.

        The mod waits for the room transition to complete (up to 10 s).

        Returns updated game state in data.state (new screen after travel).

        Common errors:
            invalid_action – not on map screen or no nodes available.
            invalid_request – option_index missing.
            invalid_target – option_index out of range.
        """
        return sts2.choose_map_node(option_index=option_index)

    @mcp.tool
    def collect_rewards_and_proceed() -> dict[str, Any]:
        """Auto-collect all rewards and advance past the reward screen.

        Behavior:
            - Claims each available reward (gold, relic, potion — skips potions
              if potion slots are full).
            - When a card reward appears, auto-selects the first offered card.
            - Clicks "Proceed" when finished.

        Use this for hands-off progression. For deliberate deck-building
        decisions, use claim_reward / choose_reward_card / skip_reward_cards
        individually instead.

        Preconditions:
            - screen is REWARD.
            - available_actions includes "collect_rewards_and_proceed".

        Common errors:
            invalid_action – not on a reward screen.
        """
        return sts2.collect_rewards_and_proceed()

    @mcp.tool
    def claim_reward(option_index: int) -> dict[str, Any]:
        """Claim a single reward item on the reward screen.

        Args:
            option_index: zero-based index into reward.rewards[] where
                          claimable=true.

        This covers gold, potions, relics, and card-reward entries. If the
        chosen reward is a card reward, the screen transitions to the card
        selection sub-screen — then use choose_reward_card or skip_reward_cards.

        Preconditions:
            - screen is REWARD.
            - available_actions includes "claim_reward".
            - reward.rewards is non-empty with at least one claimable item.

        Common errors:
            invalid_action – no claimable rewards.
            invalid_request – option_index missing.
            invalid_target – option_index out of range.
        """
        return sts2.claim_reward(option_index=option_index)

    @mcp.tool
    def choose_reward_card(option_index: int) -> dict[str, Any]:
        """Pick a card from the card reward selection screen.

        Args:
            option_index: zero-based index into reward.card_options[].

        Preconditions:
            - screen is REWARD.
            - reward.pending_card_choice is true.
            - available_actions includes "choose_reward_card".

        After choosing, the screen returns to the main reward list or proceeds
        automatically.

        Common errors:
            invalid_action – not on card reward sub-screen.
            invalid_request – option_index missing.
            invalid_target – option_index out of range.
        """
        return sts2.choose_reward_card(option_index=option_index)

    @mcp.tool
    def skip_reward_cards() -> dict[str, Any]:
        """Skip the card reward without picking any card.

        Preconditions:
            - screen is REWARD.
            - reward.pending_card_choice is true.
            - reward.alternatives is non-empty (contains the skip button).
            - available_actions includes "skip_reward_cards".

        Common errors:
            invalid_action – no skip button visible or not on card reward screen.
        """
        return sts2.skip_reward_cards()

    @mcp.tool
    def select_deck_card(option_index: int) -> dict[str, Any]:
        """Select a card on the deck card selection screen.

        Args:
            option_index: zero-based index into selection.cards[].

        Currently supports single-card selection with auto-confirm, primarily
        used for card removal. The selection.prompt field describes what the
        screen is asking (e.g. "Choose a card to remove").

        Preconditions:
            - screen is CARD_SELECTION.
            - available_actions includes "select_deck_card".
            - selection.cards is non-empty.

        Common errors:
            invalid_action – not on a card selection screen.
            invalid_request – option_index missing.
            invalid_target – option_index out of range.
        """
        return sts2.select_deck_card(option_index=option_index)

    @mcp.tool
    def proceed() -> dict[str, Any]:
        """Click the "Proceed" / "Continue" button on the current screen.

        Works on any non-combat screen that has a visible, enabled ProceedButton
        (e.g. chest rooms, rest sites after action, event aftermath).

        Do NOT use this on reward screens — use collect_rewards_and_proceed or
        the individual reward tools instead.

        Preconditions:
            - available_actions includes "proceed".
            - A ProceedButton is visible and enabled.

        Common errors:
            invalid_action – no proceed button on the current screen.
        """
        return sts2.proceed()

    return mcp


def main() -> None:
    create_server().run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
