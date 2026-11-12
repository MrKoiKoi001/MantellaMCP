class companion_constants:
    """Keys and values shared by the game route, the MCP route and the Papyrus side.

    The Papyrus scripts read and write these exact strings, so changing one here
    means changing it in the game mod as well.
    """
    PREFIX: str = "companion_"

    ROUTE_GAME: str = "/companion"
    ROUTE_MCP_PREFIX: str = "/mcp/"

    # Game -> server (long polling request)
    # Number of the request. The server sends it back unchanged, so the game can tell replies to older requests apart
    KEY_POLL_ID: str = PREFIX + "poll_id"
    # Only in requests that answer a report_state command, and in the first one after the game loaded
    KEY_STATE: str = PREFIX + "state"
    KEY_RESULTS: str = PREFIX + "results"
    KEY_RESULT_OK: str = PREFIX + "ok"
    KEY_RESULT_MESSAGE: str = PREFIX + "message"

    # Server -> game (long polling reply)
    KEY_REPLY_TYPE: str = PREFIX + "reply_type"
    REPLY_TYPE_IDLE: str = PREFIX + "idle"
    REPLY_TYPE_COMMANDS: str = PREFIX + "commands"
    KEY_COMMANDS: str = PREFIX + "commands"
    KEY_COMMAND_ID: str = PREFIX + "command_id"
    KEY_COMMAND_ACTION: str = PREFIX + "action"
    KEY_COMMAND_TARGET: str = PREFIX + "target"
    # The same ref_id as a number, cut into its upper and lower 16 bits (see ref_ids.split_form_id). Both 0 without a target
    KEY_COMMAND_TARGET_HIGH: str = PREFIX + "target_high"
    KEY_COMMAND_TARGET_LOW: str = PREFIX + "target_low"
    KEY_COMMAND_DETAIL: str = PREFIX + "detail"
    # SKSE_HTTP sends every reply to all listening scripts. If Mantella is installed next to the companion mod,
    # MantellaConversation.psc reads our replies too and treats them as an error unless it finds exactly this value
    KEY_MANTELLA_REPLY_TYPE: str = "mantella_reply_type"
    MANTELLA_REPLY_TYPE_IGNORED: str = "mantella_init_completed"

    # Actions the companion can be told to do
    ACTION_FOLLOW: str = "follow"
    ACTION_WAIT: str = "wait"
    ACTION_ATTACK: str = "attack"
    ACTION_STAND_DOWN: str = "stand_down"
    ACTION_FLEE: str = "flee"
    ACTION_MOVE_TO: str = "move_to"
    ACTION_CAST_SPELL: str = "cast_spell"
    # Internal: makes the game collect its state and send it with its next request
    ACTION_REPORT_STATE: str = "report_state"

    PLAYER_ACTIONS: list[str] = [
        ACTION_FOLLOW,
        ACTION_WAIT,
        ACTION_ATTACK,
        ACTION_STAND_DOWN,
        ACTION_FLEE,
        ACTION_MOVE_TO,
        ACTION_CAST_SPELL,
    ]
    ACTIONS_REQUIRING_TARGET: list[str] = [ACTION_ATTACK, ACTION_MOVE_TO]

    # Result status values returned to the MCP side
    STATUS_DONE: str = "done"
    STATUS_FAILED: str = "failed"
    STATUS_DELIVERED: str = "delivered_no_result_yet"
    STATUS_NOT_DELIVERED: str = "not_delivered"
    STATUS_GAME_NOT_CONNECTED: str = "game_not_connected"
