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
    # Extra text for the action. For report_state: whose inventory the state should carry (see INVENTORY_...), or empty
    KEY_COMMAND_DETAIL: str = PREFIX + "detail"
    # For give and take: how many of the item, 0 = all of them
    KEY_COMMAND_COUNT: str = PREFIX + "count"
    # For report_state: 1 if the state should carry the things close to the player (furniture, containers, loose items).
    # KEY_COMMAND_COUNT then says how many characters (and things of each kind) the state should list at most
    KEY_COMMAND_THINGS: str = PREFIX + "things"
    # For report_state: 1 if the lists of the state (characters, things, places, quest targets) should be seen from
    # the companion instead of from the player
    KEY_COMMAND_AROUND: str = PREFIX + "around"
    # For report_state: 1 if the state should carry the level, the skills and so on of the player
    KEY_COMMAND_STATS: str = PREFIX + "stats"
    # For report_state: 1 if the list of characters should only hold those the viewer can see
    KEY_COMMAND_VISIBLE: str = PREFIX + "visible"
    # What the AI can name as the centre of those lists
    AROUND_PLAYER: str = "player"
    AROUND_COMPANION: str = "you"
    AROUND_CHOICES: list[str] = [AROUND_PLAYER, AROUND_COMPANION]
    INVENTORY_OWN: str = "own"
    INVENTORY_PLAYER: str = "player"
    INVENTORY_BOTH: str = "both"
    # Internal: what the dead character carries or the container holds that the target of the report_state command names
    INVENTORY_CORPSE: str = "corpse"
    # For loot: the ID of the one item to take from the body, cut in two like the target. Both 0 = everything.
    # For gesture: the ID of the animation to play (see gestures.py). Both 0 = end the gesture
    KEY_COMMAND_ITEM_HIGH: str = PREFIX + "item_high"
    KEY_COMMAND_ITEM_LOW: str = PREFIX + "item_low"
    INVENTORY_CHOICES: list[str] = [INVENTORY_OWN, INVENTORY_PLAYER, INVENTORY_BOTH]
    COUNT_ALL_TEXT: str = "all"
    COUNT_ALL: int = 0
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
    # The companion walks to a door that leads into another area and goes through it on its own
    ACTION_ENTER: str = "enter"
    # The companion steps a few meters out of the way. For the game: 'count' is the number of the direction (see
    # ROOM_DIRECTIONS), and how far in meters is where loot has the lower half of an item ID
    ACTION_MAKE_ROOM: str = "make_room"
    # The directions by the numbers the game mod knows them by. Back is away from the player,
    # left and right are as the player sees them who looks at the companion
    ROOM_DIRECTION_BACK: str = "back"
    ROOM_DIRECTIONS: dict[str, int] = {ROOM_DIRECTION_BACK: 0, "left": 1, "right": 2}
    ROOM_DEFAULT_METERS: int = 3
    ROOM_HIGHEST_METERS: int = 10
    ACTION_LOOT: str = "loot"
    ACTION_SIT: str = "sit"
    ACTION_LOOK_AT: str = "look_at"
    ACTION_GESTURE: str = "gesture"
    # For gesture: what 'detail' says to end a gesture that goes on and on
    GESTURE_STOP_TEXT: str = "stop"
    # The companion's face takes on a look. For the game: 'count' is the number of the look (EXPRESSION_NONE = its own
    # face again), and how strong in percent is where loot has the lower half of an item ID
    ACTION_EXPRESSION: str = "expression"
    # The looks by the numbers the game gives them (Actor.SetExpressionOverride, the "mood" and "combat" ones)
    EXPRESSIONS: dict[str, int] = {
        "neutral": 7, "angry": 8, "afraid": 9, "happy": 10, "sad": 11, "surprised": 12, "puzzled": 13, "disgusted": 14,
        "furious": 15, "shouting": 16,
    }
    EXPRESSION_NONE_TEXTS: list[str] = ["none", "off", "clear", "stop", "normal"]
    EXPRESSION_NONE: int = -1
    EXPRESSION_FULL_STRENGTH: int = 100
    # The companion speaks the text in 'detail' aloud. For the game: 'count' is the number of the line to speak (see voice.py)
    ACTION_SAY: str = "say"
    ACTION_EQUIP: str = "equip"
    ACTION_UNEQUIP: str = "unequip"
    ACTION_GIVE: str = "give"
    ACTION_TAKE: str = "take"
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
        ACTION_ENTER,
        ACTION_MAKE_ROOM,
        ACTION_LOOT,
        ACTION_SIT,
        ACTION_LOOK_AT,
        ACTION_GESTURE,
        ACTION_EXPRESSION,
        ACTION_SAY,
        ACTION_EQUIP,
        ACTION_UNEQUIP,
        ACTION_GIVE,
        ACTION_TAKE,
        ACTION_CAST_SPELL,
    ]
    # Their target is the ID of an item from an inventory instead of the ref_id of a character
    ITEM_ACTIONS: list[str] = [ACTION_EQUIP, ACTION_UNEQUIP, ACTION_GIVE, ACTION_TAKE]
    # 'detail' says how many
    ACTIONS_WITH_COUNT: list[str] = [ACTION_GIVE, ACTION_TAKE]
    ACTIONS_REQUIRING_TARGET: list[str] = [ACTION_ATTACK, ACTION_MOVE_TO, ACTION_ENTER, ACTION_LOOT] + ITEM_ACTIONS

    # Result status values returned to the MCP side
    STATUS_DONE: str = "done"
    STATUS_FAILED: str = "failed"
    STATUS_DELIVERED: str = "delivered_no_result_yet"
    STATUS_NOT_DELIVERED: str = "not_delivered"
    STATUS_GAME_NOT_CONNECTED: str = "game_not_connected"
