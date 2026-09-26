"""Shared Game Mode identifiers and compatibility validation."""

GAME_STANDARD = 0
GAME_COOP = 1
GAME_QUICK = 2
GAME_MUNCHIES = 3
GAME_MULTIBALL_MAYHEM = 4

GAME_MODE_NAMES = (
    "STANDARD GAME",
    "CO-OP GAME",
    "QUICK GAME",
    "MUNCHIES CHALLENGE",
    "MULTIBALL MAYHEM",
)

GAME_MODE_COUNT = len(GAME_MODE_NAMES)
GAME_MODE_MASK_ALL = (1 << GAME_MODE_COUNT) - 1
GAME_MODE_MASK_ONE_PLAYER = GAME_MODE_MASK_ALL & ~(1 << GAME_COOP)


def availability_mask_for_players(player_count: int) -> int:
    return GAME_MODE_MASK_ALL if int(player_count) >= 2 else GAME_MODE_MASK_ONE_PLAYER


def sanitize_availability_mask(mask: int) -> int:
    """Keep known bits and guarantee the Standard compatibility fallback."""
    return (int(mask) & GAME_MODE_MASK_ALL) | (1 << GAME_STANDARD)


def normalize_game_mode(mode_id: int, availability_mask: int, player_count=None) -> int:
    """Return a safe mode id; unknown or unavailable values become Standard."""
    mask = sanitize_availability_mask(availability_mask)
    try:
        mode_id = int(mode_id)
    except (TypeError, ValueError):
        return GAME_STANDARD
    if not 0 <= mode_id < GAME_MODE_COUNT:
        return GAME_STANDARD
    if player_count == 1 and mode_id == GAME_COOP:
        return GAME_STANDARD
    if not mask & (1 << mode_id):
        return GAME_STANDARD
    return mode_id


def game_mode_available(mode_id: int, availability_mask: int) -> bool:
    return (
        0 <= mode_id < GAME_MODE_COUNT
        and bool(sanitize_availability_mask(availability_mask) & (1 << mode_id))
    )
