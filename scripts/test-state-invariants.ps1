param(
    [string]$BaseUrl = "http://127.0.0.1:8080",
    [int]$TimeoutSec = 5
)

$ErrorActionPreference = "Stop"

function Invoke-JsonEndpoint {
    param(
        [string]$Path
    )

    $response = Invoke-WebRequest -Uri ($BaseUrl.TrimEnd("/") + $Path) -UseBasicParsing -TimeoutSec $TimeoutSec
    return $response.Content | ConvertFrom-Json
}

function Add-MissingActionFailure {
    param(
        [System.Collections.Generic.List[string]]$Failures,
        [System.Collections.Generic.HashSet[string]]$ActionSet,
        [string]$ActionName,
        [string]$Reason
    )

    if (-not $ActionSet.Contains($ActionName)) {
        $Failures.Add("missing action '$ActionName': $Reason")
    }
}

function Add-ForbiddenActionFailure {
    param(
        [System.Collections.Generic.List[string]]$Failures,
        [System.Collections.Generic.HashSet[string]]$ActionSet,
        [string]$ActionName,
        [string]$Reason
    )

    if ($ActionSet.Contains($ActionName)) {
        $Failures.Add("unexpected action '$ActionName': $Reason")
    }
}

$stateResponse = Invoke-JsonEndpoint -Path "/state"
$actionsResponse = Invoke-JsonEndpoint -Path "/actions/available"

$state = $stateResponse.data
$actionSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
foreach ($action in @($actionsResponse.data.actions)) {
    if ($null -ne $action -and $null -ne $action.name) {
        [void]$actionSet.Add([string]$action.name)
    }
}

$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

if ($null -ne $state.selection -and @($state.selection.cards).Count -gt 0) {
    Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "select_deck_card" -Reason "selection.cards[] is populated"
}

if ($null -ne $state.reward) {
    Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "collect_rewards_and_proceed" -Reason "reward payload is present"
    Add-ForbiddenActionFailure -Failures $failures -ActionSet $actionSet -ActionName "proceed" -Reason "reward flows should use reward-specific actions instead of proceed"

    if ($state.reward.pending_card_choice) {
        if (@($state.reward.card_options).Count -gt 0) {
            Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "choose_reward_card" -Reason "reward.card_options[] is populated"
        }

        if (@($state.reward.alternatives).Count -gt 0) {
            Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "skip_reward_cards" -Reason "reward.alternatives[] is populated"
        }
    }
    else {
        if (@($state.reward.rewards | Where-Object { $_.claimable }).Count -gt 0) {
            Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "claim_reward" -Reason "reward.rewards[] still contains claimable items"
        }
    }
}

if ($null -ne $state.map -and @($state.map.available_nodes).Count -gt 0) {
    Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "choose_map_node" -Reason "map.available_nodes[] is populated"
}

if ($null -ne $state.chest) {
    if (-not $state.chest.is_opened) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "open_chest" -Reason "chest is present and not yet opened"
    }

    if ((@($state.chest.relic_options).Count -gt 0) -and (-not $state.chest.has_relic_been_claimed)) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "choose_treasure_relic" -Reason "chest.relic_options[] is populated"
    }

    if (($actionSet.Contains("proceed")) -and (-not $state.chest.has_relic_been_claimed)) {
        $failures.Add("chest.has_relic_been_claimed should be true before proceed is exposed")
    }
}

if ($null -ne $state.event -and @($state.event.options | Where-Object { -not $_.is_locked }).Count -gt 0) {
    Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "choose_event_option" -Reason "event has unlocked options"
}

if ($null -ne $state.rest -and @($state.rest.options | Where-Object { $_.is_enabled }).Count -gt 0) {
    Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "choose_rest_option" -Reason "rest.options[] has enabled entries"
}

if ($null -ne $state.shop) {
    if ($state.shop.can_open) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "open_shop_inventory" -Reason "shop.can_open=true"
    }

    if ($state.shop.can_close) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "close_shop_inventory" -Reason "shop.can_close=true"
    }

    if ($state.shop.is_open) {
        if (@($state.shop.cards | Where-Object { $_.is_stocked -and $_.enough_gold }).Count -gt 0) {
            Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "buy_card" -Reason "shop.is_open=true and shop.cards[] has purchasable entries"
        }

        if (@($state.shop.relics | Where-Object { $_.is_stocked -and $_.enough_gold }).Count -gt 0) {
            Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "buy_relic" -Reason "shop.is_open=true and shop.relics[] has purchasable entries"
        }

        if (@($state.shop.potions | Where-Object { $_.is_stocked -and $_.enough_gold }).Count -gt 0) {
            Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "buy_potion" -Reason "shop.is_open=true and shop.potions[] has purchasable entries"
        }

        if ($null -ne $state.shop.card_removal -and $state.shop.card_removal.available -and $state.shop.card_removal.enough_gold -and (-not $state.shop.card_removal.used)) {
            Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "remove_card_at_shop" -Reason "shop.is_open=true and shop.card_removal is available and affordable"
        }
    }
}

if ($null -ne $state.character_select) {
    if (@($state.character_select.characters | Where-Object { -not $_.is_locked }).Count -gt 0) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "select_character" -Reason "character_select has unlocked choices"
    }

    if ($state.character_select.can_embark) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "embark" -Reason "character_select.can_embark=true"
    }
}

if ($null -ne $state.timeline) {
    if ($state.timeline.can_choose_epoch) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "choose_timeline_epoch" -Reason "timeline.can_choose_epoch=true"
    }

    if ($state.timeline.can_confirm_overlay) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "confirm_timeline_overlay" -Reason "timeline.can_confirm_overlay=true"
    }

    if ($state.timeline.back_enabled) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "close_main_menu_submenu" -Reason "timeline.back_enabled=true"
    }
}

if ($null -ne $state.modal) {
    if ($state.modal.can_confirm) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "confirm_modal" -Reason "modal.can_confirm=true"
    }

    if ($state.modal.can_dismiss) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "dismiss_modal" -Reason "modal.can_dismiss=true"
    }
}

if ($null -ne $state.game_over -and $state.game_over.can_return_to_main_menu) {
    Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "return_to_main_menu" -Reason "game_over.can_return_to_main_menu=true"
}

if ($state.in_combat -and $null -ne $state.combat) {
    if (@($state.combat.hand | Where-Object { $_.playable }).Count -gt 0) {
        Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "play_card" -Reason "combat.hand[] has playable cards"
    }

    if ($null -ne $state.combat.player) {
        $orbCount = @($state.combat.player.orbs).Count
        $orbCapacity = [int]$state.combat.player.orb_capacity
        $emptyOrbSlots = [int]$state.combat.player.empty_orb_slots

        if ($orbCapacity -lt 0) {
            $failures.Add("combat.player.orb_capacity should never be negative")
        }

        if ($orbCount -gt $orbCapacity) {
            $failures.Add("combat.player.orbs[] count exceeds combat.player.orb_capacity")
        }

        if ($emptyOrbSlots -ne ($orbCapacity - $orbCount)) {
            $failures.Add("combat.player.empty_orb_slots does not match orb_capacity - orbs.Count")
        }

        $expectedSlotIndex = 0
        foreach ($orb in @($state.combat.player.orbs)) {
            if ($orb.slot_index -ne $expectedSlotIndex) {
                $failures.Add("combat.player.orbs[] slot_index values must stay contiguous and zero-based")
                break
            }

            if ([string]::IsNullOrWhiteSpace([string]$orb.orb_id)) {
                $failures.Add("combat.player.orbs[] entries must expose orb_id")
                break
            }

            $expectedSlotIndex++
        }
    }
}

if (@($state.run.potions | Where-Object { $_.can_use }).Count -gt 0) {
    Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "use_potion" -Reason "run.potions[] has usable entries"
}

if (@($state.run.potions | Where-Object { $_.can_discard }).Count -gt 0) {
    Add-MissingActionFailure -Failures $failures -ActionSet $actionSet -ActionName "discard_potion" -Reason "run.potions[] has discardable entries"
}

foreach ($potion in @($state.run.potions)) {
    if ($null -eq $potion -or -not $potion.occupied) {
        continue
    }

    if ($potion.target_type -eq "TargetedNoCreature" -and $potion.requires_target) {
        $failures.Add("potion '$($potion.potion_id)' should not require target_index when target_type=TargetedNoCreature")
    }
}

if ($null -ne $state.run) {
    if ([string]::IsNullOrWhiteSpace([string]$state.run.character_id)) {
        $failures.Add("run.character_id should always be populated when run payload exists")
    }

    if ([string]::IsNullOrWhiteSpace([string]$state.run.character_name)) {
        $failures.Add("run.character_name should always be populated when run payload exists")
    }

    if ([int]$state.run.base_orb_slots -lt 0) {
        $failures.Add("run.base_orb_slots should never be negative")
    }
}

$summary = [pscustomobject]@{
    screen = $state.screen
    checked_actions = @($actionsResponse.data.actions).Count
    failure_count = $failures.Count
    warning_count = $warnings.Count
    failures = @($failures)
    warnings = @($warnings)
}

if ($failures.Count -gt 0) {
    $summary | ConvertTo-Json -Depth 5
    exit 1
}

$summary | ConvertTo-Json -Depth 5
