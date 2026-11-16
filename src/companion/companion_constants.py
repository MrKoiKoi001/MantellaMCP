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
    # With every request, from a game mod that knows chains of commands: 1 while the companion is in the middle of
    # something that takes time (a walk, sitting down, getting on a horse), and how many things it started have failed so far
    KEY_BUSY: str = PREFIX + "busy"
    KEY_FAILS: str = PREFIX + "fails"
    # With a request, if there are any, from a game mod whose plugin keeps events: the urgent events since the request
    # before (a fight begins, someone dies), one text per event (see game_state.read_events)
    KEY_EVENTS: str = PREFIX + "events"
    # Which menus were open in the game since its request before (see game_state.read_menus). Only for the log
    KEY_MENUS: str = PREFIX + "menus"
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
    # For report_state: 1 if the state should carry what is around the companion as rays find it (walls, drops)
    KEY_COMMAND_SPACE: str = PREFIX + "space"
    # For report_state with 'space': how far the rays reach in meters (0 = SPACE_DEFAULT_METERS), the direction they look
    # into in degrees (SPACE_NO_DIRECTION = all around, beginning straight ahead of the companion), 1 if those degrees
    # count from where the companion faces (otherwise from north), and over how many degrees the rays fan out
    KEY_COMMAND_SPACE_METERS: str = PREFIX + "space_meters"
    KEY_COMMAND_SPACE_DEGREES: str = PREFIX + "space_degrees"
    KEY_COMMAND_SPACE_FACING: str = PREFIX + "space_facing"
    KEY_COMMAND_SPACE_ANGLE: str = PREFIX + "space_angle"
    SPACE_NO_DIRECTION: int = -1
    SPACE_DEFAULT_METERS: int = 15
    SPACE_HIGHEST_METERS: int = 200
    SPACE_FULL_CIRCLE_DEGREES: int = 360
    # How wide the rays fan out if the AI names a direction but no angle
    SPACE_DEFAULT_LOOK_DEGREES: int = 60
    # For report_state: a name, or a part of one, to look for in the whole loaded area (empty = no search)
    KEY_COMMAND_FIND: str = PREFIX + "find"
    # For report_state: the ID of a book or a note to read (as text, and cut in two like the target; empty = none),
    # and the page of it (from 1)
    KEY_COMMAND_READ: str = PREFIX + "read"
    KEY_COMMAND_READ_HIGH: str = PREFIX + "read_high"
    KEY_COMMAND_READ_LOW: str = PREFIX + "read_low"
    KEY_COMMAND_READ_PAGE: str = PREFIX + "read_page"
    # What the AI can name as the centre of those lists
    AROUND_PLAYER: str = "player"
    AROUND_COMPANION: str = "you"
    AROUND_CHOICES: list[str] = [AROUND_PLAYER, AROUND_COMPANION]
    INVENTORY_OWN: str = "own"
    INVENTORY_PLAYER: str = "player"
    INVENTORY_BOTH: str = "both"
    # Internal: what the dead character carries or the container holds that the target of the report_state command names
    INVENTORY_CORPSE: str = "corpse"
    # Internal, like INVENTORY_CORPSE: what the merchant in the target offers and what the companion could sell to it
    INVENTORY_MERCHANT: str = "merchant"
    # For loot: the ID of the one item to take from the body, cut in two like the target. Both 0 = everything.
    # For gesture: the ID of the animation to play (see gestures.py). Both 0 = end the gesture
    KEY_COMMAND_ITEM_HIGH: str = PREFIX + "item_high"
    KEY_COMMAND_ITEM_LOW: str = PREFIX + "item_low"
    INVENTORY_CHOICES: list[str] = [INVENTORY_OWN, INVENTORY_PLAYER, INVENTORY_BOTH]
    # STATE with an inventory: only a part of it, as a short list (the argument 'filter'). The game mod gets the word
    # with report_state and only collects that part where it can, a game mod that does not know it sends everything
    KEY_COMMAND_INVENTORY_FILTER: str = "companion_inventory_filter"
    FILTER_ALL: str = "all"
    FILTER_FAVORITES: str = "favorites"
    FILTER_EQUIPPED: str = "equipped"
    INVENTORY_FILTERS: list[str] = [FILTER_FAVORITES, FILTER_EQUIPPED, FILTER_ALL]
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
    # For move_to: 'count' is how many meters in front of the target the companion stops. MOVE_TO_USUAL_DISTANCE lets
    # the game mod decide: a little away from a character (MOVE_TO_PERSON_METERS), right up to a thing
    MOVE_TO_USUAL_DISTANCE: int = -1
    MOVE_TO_PERSON_METERS: int = 2
    MOVE_TO_HIGHEST_KEEP_METERS: int = 10
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
    # The companion turns to the target, or to the point of the compass in 'detail'. For the game: 'count' is that
    # point in degrees (FACE_NO_DIRECTION = turn to the target)
    ACTION_FACE: str = "face"
    FACE_NO_DIRECTION: int = -1
    # The companion walks a number of meters in a direction and waits there. For the game: 'count' is the direction in
    # degrees, the upper half of the item ID is 1 if it counts from where the companion faces (0 = from north),
    # and the lower half is how far in meters
    ACTION_MOVE: str = "move"
    MOVE_DEFAULT_METERS: int = 5
    MOVE_HIGHEST_METERS: int = 30
    DEGREES_PER_DIRECTION: int = 45
    # The points of the compass clockwise from north, and the directions seen from the companion clockwise from
    # straight ahead. The same words STATE uses. Their place in the list times DEGREES_PER_DIRECTION is the angle
    COMPASS_NAMES: list[str] = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
    RELATIVE_NAMES: list[str] = ["ahead", "ahead-right", "right", "behind-right", "behind", "behind-left", "left", "ahead-left"]
    # What the AI may write as well, with the word above it stands for
    COMPASS_OTHER_WORDS: dict[str, str] = {
        "n": "north", "ne": "north-east", "e": "east", "se": "south-east", "s": "south", "sw": "south-west", "w": "west", "nw": "north-west",
        "northeast": "north-east", "southeast": "south-east", "southwest": "south-west", "northwest": "north-west",
        "nord": "north", "nordost": "north-east", "no": "north-east", "ost": "east", "o": "east", "südost": "south-east",
        "suedost": "south-east", "so": "south-east", "süd": "south", "sued": "south", "südwest": "south-west",
        "suedwest": "south-west", "nordwest": "north-west",
    }
    RELATIVE_OTHER_WORDS: dict[str, str] = {
        "forward": "ahead", "forwards": "ahead", "back": "behind", "backward": "behind", "backwards": "behind",
        "right-ahead": "ahead-right", "left-ahead": "ahead-left", "right-behind": "behind-right", "left-behind": "behind-left",
    }
    # How the answer of the game puts the directions seen from the companion ("You walk about 5 meters ...")
    RELATIVE_PHRASES: list[str] = ["straight ahead", "ahead to the right", "to your right", "back to the right", "back",
                                   "back to the left", "to your left", "ahead to the left"]
    # The companion walks to a thing that lies around and picks it up
    ACTION_PICK_UP: str = "pick_up"
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
    # say: the animation event of the game that moves the hands of the companion while it talks ("" = none).
    # The server picks one of TALK_EVENTS for every sentence if the setting talk_gestures is on
    KEY_COMMAND_TALK: str = "companion_talk"
    # For gesture: the event that ends the animation, the animation for sitting (ID in two halves, its event and the
    # event that ends it), and 1 if the gesture is a state that stays on until it is ended (being drunk).
    # KEY_COMMAND_TALK carries the event of the animation for standing, for the case that the game refuses to play it by its ID
    KEY_COMMAND_STOP_EVENT: str = "companion_stop_event"
    KEY_COMMAND_SIT_ITEM_HIGH: str = "companion_sit_item_high"
    KEY_COMMAND_SIT_ITEM_LOW: str = "companion_sit_item_low"
    KEY_COMMAND_SIT_EVENT: str = "companion_sit_event"
    KEY_COMMAND_SIT_STOP_EVENT: str = "companion_sit_stop_event"
    KEY_COMMAND_GESTURE_STATE: str = "companion_gesture_state"
    # report_state: 1 = the game mod is to cut off the sentences the game lets the companion say on its own
    # (the setting mute_game_lines)
    KEY_COMMAND_MUTE: str = "companion_mute"
    TALK_EVENTS: list[str] = ["IdleDialogueMovingTalkA", "IdleDialogueMovingTalkB", "IdleDialogueMovingTalkC", "IdleDialogueMovingTalkD"]
    # say without a manner: the animations for the hands the server picks from, never the same one twice in a row.
    # An animation is the name of an animation event of the game, or the ID of an animation ("Idle") of the game
    # as eight hex digits (the IDs come from data/Skyrim/skyrim_idles.csv, like those of gesture)
    TALK_ANIMATIONS: list[str] = TALK_EVENTS + ["IdleDialogueExpressiveStart"]
    # say with 'manner' = how the companion speaks: the animations for its hands and the look on its face (a name from
    # EXPRESSIONS, or empty) while it speaks. Change this table to try other animations, the game mod plays what it gets.
    # How each of them looks in the game is not documented anywhere, so this is a first pick to be corrected by eye
    MANNERS: dict[str, tuple[list[str], str]] = {
        "calm": (TALK_EVENTS, ""),
        "explain": (["IdleDialogueExpressiveStart", "IdleDialogueMovingTalkB", "IdleDialogueMovingTalkD"], ""),
        "excited": (["IdleDialogueHappyStart", "IdleDialogueExpressiveStart"], "happy"),
        "angry": (["IdleDialogueAngryStart"], "angry"),
        "sad": ([], "sad"),
        # No hands and no look: only the lips, and the eyes on whom the companion speaks to
        "none": ([], ""),
        "joking": (["IdleDialogueHappyStart", "IdleDialogueMovingTalkA", "IdleDialogueMovingTalkC"], "happy"),
        # IdlePointClose and IdlePointFar_02
        "pointing": (["000FFA0C", "000B8F1F"], ""),
        # IdleStudy
        "thinking": (["000977ED"], "puzzled"),
    }
    # say: a sentence that lasts longer than this gets another animation every so many seconds (a guess to be tuned),
    # but only if at least TALK_GESTURE_LEAST_SECONDS of the sentence are left for it
    TALK_GESTURE_SECONDS: float = 4.0
    TALK_GESTURE_LEAST_SECONDS: float = 2.0
    # say: how long after the end of the sound the server tells the game that the sentence is over
    SAY_END_EXTRA_SECONDS: float = 0.4
    # say: two commands only the server sends: the next animation of a long sentence, and the end of the sentence
    # (the eyes, the face and the stance of the companion go back to what they were). Game mods from this protocol on know them
    ACTION_SAY_TALK: str = "say_talk"
    ACTION_SAY_END: str = "say_end"
    SAY_END_PROTOCOL: int = 8
    # say, say_talk and say_end: the number of the sentence, so that an end that comes late does not end the next sentence
    KEY_COMMAND_SAY_NUMBER: str = "companion_say_number"
    # say: the number of the look the face takes on while the companion speaks (FACE_NONE = none)
    KEY_COMMAND_FACE: str = "companion_face"
    # say to a character: the number of the look the face of that character takes on while the companion speaks to it.
    # It also keeps its eyes on the companion for that long. Left out = its face stays as it is
    KEY_COMMAND_LISTENER_FACE: str = "companion_listener_face"
    # Which look that is, by how the companion speaks (a name from MANNERS -> a name from EXPRESSIONS)
    LISTENER_FACES: dict[str, str] = {"angry": "surprised", "excited": "happy", "joking": "happy", "sad": "sad", "thinking": "puzzled"}
    # say: how long the sound of the sentence is in milliseconds. The game mod needs it when the sentence starts later,
    # because the companion first turns to or walks over to another character
    KEY_COMMAND_SAY_MS: str = "companion_say_ms"
    # say: 1 = the companion says it in a low voice (the argument 'quiet' of COMMAND). The sound is quieter already,
    # the game mod makes the look on the face weaker and answers "You say it quietly."
    KEY_COMMAND_QUIET: str = "companion_quiet"
    # say: 1 = the companion calls it out (the argument 'loud' of COMMAND). The sound is louder already, the game mod
    # makes more noise with it for characters that listen for enemies. In a low voice it makes none
    KEY_COMMAND_LOUD: str = "companion_loud"
    # move_to, move, enter and follow: 1 = the companion runs instead of walking (the argument 'run' of COMMAND)
    KEY_COMMAND_RUN: str = "companion_run"
    FACE_NONE: int = -1
    # The companion puts its weapon away and keeps it away although the player draws one, until draw
    ACTION_SHEATHE: str = "sheathe"
    ACTION_DRAW: str = "draw"
    # The companion opens or closes a door that only swings (no door into another area). Without a target: the nearest one
    ACTION_OPEN: str = "open"
    ACTION_CLOSE: str = "close"
    ACTION_EQUIP: str = "equip"
    ACTION_UNEQUIP: str = "unequip"
    # The companion marks a weapon, a shield or a piece of armor of its own as a favorite ('detail' on) or takes the
    # mark off again (off). Of its own accord it only switches between its favorites then. For the game: 'count' 1 = on, 0 = off
    ACTION_FAVORITE: str = "favorite"
    FAVORITE_ON: str = "on"
    FAVORITE_OFF: str = "off"
    ACTION_GIVE: str = "give"
    ACTION_TAKE: str = "take"
    # give and take with several items in one command: a list of numbers, VALUES_PER_HAND_OVER per item: the two
    # halves of its ID and how many (COUNT_ALL = all of them). Game mods from HAND_OVER_PROTOCOL on know it.
    # The game mod moves its hand once and answers with what changed hands and what did not
    KEY_COMMAND_ITEMS: str = PREFIX + "items"
    VALUES_PER_HAND_OVER: int = 3
    HAND_OVER_PROTOCOL: int = 12
    HAND_OVER_HIGHEST_ITEMS: int = 60
    # Every item costs the game a few frames, so the answer to a long list takes longer than to other commands
    HAND_OVER_TIMEOUT_SECONDS: float = 30.0
    # give with this in 'detail': everything the companion neither wears, holds nor has marked as a favorite, apart
    # from what belongs to these groups of the inventory (see game_state.ITEM_GROUPS). Gold is no item of the lists
    HAND_OVER_JUNK: str = "junk"
    JUNK_KEPT_GROUPS: list[str] = ["Ammunition", "Potions and food", "Keys"]
    ACTION_CAST_SPELL: str = "cast_spell"
    # cast_spell: for how many seconds the companion holds a spell that is held as long as the hand stays up (flames,
    # healing hands). 0 = the game mod decides (CAST_HOLD_SECONDS). The AI writes the seconds behind the name of the spell
    KEY_COMMAND_SECONDS: str = PREFIX + "seconds"
    CAST_HOLD_SECONDS: int = 3
    CAST_HOLD_HIGHEST_SECONDS: int = 15
    # Riding: take a horse as the companion's own, get on it, get off it, and whether the companion mounts and
    # dismounts together with the player (ride_lock on = it does not)
    ACTION_CLAIM_HORSE: str = "claim_horse"
    # The companion gives a horse back that it took with claim_horse: it belongs to whom it belonged before again
    ACTION_RELEASE_HORSE: str = "release_horse"
    ACTION_MOUNT: str = "mount"
    ACTION_DISMOUNT: str = "dismount"
    ACTION_RIDE_LOCK: str = "ride_lock"
    # The horse of the companion or the horse of the player runs to the player and stays there
    ACTION_CALL_HORSE: str = "call_horse"
    # The horse gets a new name, the one in 'detail'
    ACTION_RENAME_HORSE: str = "rename_horse"
    # Sneaking: the game makes a follower sneak whenever the player sneaks. 'detail' on = the companion sneaks whatever
    # the player does, off = it walks upright whatever the player does, auto = it does as the player does again
    ACTION_SNEAK: str = "sneak"
    SNEAK_AUTO: str = "auto"
    SNEAK_ON: str = "on"
    SNEAK_OFF: str = "off"
    SNEAK_CHOICES: list[str] = [SNEAK_AUTO, SNEAK_ON, SNEAK_OFF]
    # lead: the companion walks ahead of the player to a target, like move_to, and waits whenever the player falls behind
    ACTION_LEAD: str = "lead"
    # auto_heal: whether the companion heals the player and itself without being told, when their health is low.
    # 'detail' is AUTO_HEAL_ON, AUTO_HEAL_OFF or below how many percent of health it heals (which turns it on)
    ACTION_AUTO_HEAL: str = "auto_heal"
    AUTO_HEAL_ON: str = "on"
    AUTO_HEAL_OFF: str = "off"
    AUTO_HEAL_LOWEST_PERCENT: int = 10
    AUTO_HEAL_HIGHEST_PERCENT: int = 90
    # What the game mod does when the AI never said anything else
    AUTO_HEAL_USUAL_PERCENT: int = 40
    # buy and sell: trading with a merchant. The target is the merchant, 'detail' the ID of the item and how many
    ACTION_BUY: str = "buy"
    ACTION_SELL: str = "sell"
    TRADE_ACTIONS: list[str] = [ACTION_BUY, ACTION_SELL]
    # buy and sell with several items in one command carry them like give and take (KEY_COMMAND_ITEMS). Game mods
    # from this protocol on know that
    TRADE_LIST_PROTOCOL: int = 14
    # A text of at least this many characters is read as an ID, a shorter one as how many
    TRADE_ID_LENGTH: int = 6
    # pick_up with several things in one command. Either 'target' names several ref_ids: then KEY_COMMAND_ITEMS carries
    # VALUES_PER_PICK_UP numbers per thing, the two halves of its ref ID. Or 'detail' starts with PICK_UP_ALL: then
    # KEY_COMMAND_PICK_ALL says within how many meters around the target (or around the companion, without a target)
    # everything loose is picked up, and KEY_COMMAND_PICK_FILTER which kind ("" = everything). The game mod leaves out
    # what belongs to someone then. Game mods from PICK_UP_LIST_PROTOCOL on know both
    KEY_COMMAND_PICK_ALL: str = PREFIX + "pick_all"
    KEY_COMMAND_PICK_FILTER: str = PREFIX + "pick_filter"
    VALUES_PER_PICK_UP: int = 2
    PICK_UP_LIST_PROTOCOL: int = 15
    PICK_UP_ALL: str = "all"
    PICK_UP_USUAL_METERS: int = 2
    PICK_UP_HIGHEST_METERS: int = 10
    PICK_UP_HIGHEST_ITEMS: int = 30
    # The kinds the plugin of the game mod knows (GetLooseKind in plugin.cpp), and everything but clutter
    PICK_UP_KINDS: list[str] = ["potions", "food", "ingredients", "weapons", "armor", "ammo", "books", "scrolls", "keys", "gold", "misc"]
    PICK_UP_NO_CLUTTER: str = "no_clutter"
    # What the AI may write for them
    PICK_UP_KIND_WORDS: dict[str, str] = {
        "potion": "potions", "food": "food", "ingredient": "ingredients", "weapon": "weapons", "armor": "armor", "armour": "armor",
        "ammo": "ammo", "ammunition": "ammo", "arrows": "ammo", "book": "books", "scroll": "scrolls", "key": "keys", "gold": "gold",
        "coins": "gold", "misc": "misc", "other": "misc", "no clutter": PICK_UP_NO_CLUTTER, "without clutter": PICK_UP_NO_CLUTTER,
        "noclutter": PICK_UP_NO_CLUTTER,
    }
    HORSE_NAME_MAX_LENGTH: int = 40
    # call_horse: whom the horse runs to, if not to its owner
    CALL_TO_PLAYER: str = "player"
    CALL_TO_COMPANION: str = "you"
    # Only for chains of commands (see command_chain.py), the game never gets it: wait the seconds in 'detail'
    ACTION_PAUSE: str = "pause"
    RIDE_LOCK_ON: str = "on"
    RIDE_LOCK_OFF: str = "off"
    # Internal: makes the game collect its state and send it with its next request
    ACTION_REPORT_STATE: str = "report_state"

    PLAYER_ACTIONS: list[str] = [
        ACTION_FOLLOW,
        ACTION_WAIT,
        ACTION_ATTACK,
        ACTION_STAND_DOWN,
        ACTION_FLEE,
        ACTION_MOVE_TO,
        ACTION_MOVE,
        ACTION_ENTER,
        ACTION_OPEN,
        ACTION_CLOSE,
        ACTION_MAKE_ROOM,
        ACTION_FACE,
        ACTION_LOOT,
        ACTION_PICK_UP,
        ACTION_SIT,
        ACTION_LOOK_AT,
        ACTION_GESTURE,
        ACTION_EXPRESSION,
        ACTION_SAY,
        ACTION_SHEATHE,
        ACTION_DRAW,
        ACTION_EQUIP,
        ACTION_UNEQUIP,
        ACTION_FAVORITE,
        ACTION_GIVE,
        ACTION_TAKE,
        ACTION_CAST_SPELL,
        ACTION_CLAIM_HORSE,
        ACTION_RELEASE_HORSE,
        ACTION_MOUNT,
        ACTION_DISMOUNT,
        ACTION_RIDE_LOCK,
        ACTION_CALL_HORSE,
        ACTION_RENAME_HORSE,
        ACTION_SNEAK,
        ACTION_LEAD,
        ACTION_AUTO_HEAL,
        ACTION_BUY,
        ACTION_SELL,
        ACTION_PAUSE,
    ]
    # These run at once and leave the commands alone that wait in a chain. Every other command throws them away
    ACTIONS_KEEPING_QUEUE: list[str] = [
        ACTION_LOOK_AT, ACTION_GESTURE, ACTION_EXPRESSION, ACTION_SAY, ACTION_FACE, ACTION_SHEATHE, ACTION_DRAW,
        ACTION_EQUIP, ACTION_UNEQUIP, ACTION_FAVORITE, ACTION_GIVE, ACTION_TAKE, ACTION_CAST_SPELL, ACTION_RIDE_LOCK, ACTION_CLAIM_HORSE,
        ACTION_RELEASE_HORSE,
        ACTION_CALL_HORSE, ACTION_RENAME_HORSE, ACTION_SNEAK, ACTION_AUTO_HEAL,
    ]
    # Their target is the ID of an item from an inventory instead of the ref_id of a character
    ITEM_ACTIONS: list[str] = [ACTION_EQUIP, ACTION_UNEQUIP, ACTION_FAVORITE, ACTION_GIVE, ACTION_TAKE]
    # 'detail' says how many
    ACTIONS_WITH_COUNT: list[str] = [ACTION_GIVE, ACTION_TAKE]
    # These take 'run': the companion runs instead of walking
    ACTIONS_WITH_RUN: list[str] = [ACTION_MOVE_TO, ACTION_MOVE, ACTION_ENTER, ACTION_FOLLOW, ACTION_LEAD]
    ACTIONS_REQUIRING_TARGET: list[str] = [ACTION_ATTACK, ACTION_MOVE_TO, ACTION_ENTER, ACTION_LOOT, ACTION_PICK_UP, ACTION_CLAIM_HORSE,
                                           ACTION_LEAD] + ITEM_ACTIONS

    # Result status values returned to the MCP side
    STATUS_DONE: str = "done"
    # The command waits in the chain until the companion is done with what it is doing
    STATUS_QUEUED: str = "queued"
    STATUS_FAILED: str = "failed"
    STATUS_DELIVERED: str = "delivered_no_result_yet"
    STATUS_NOT_DELIVERED: str = "not_delivered"
    STATUS_GAME_NOT_CONNECTED: str = "game_not_connected"
