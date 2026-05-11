extends Node2D

const TILE_SIZE  = Globals.TILE_SIZE
const BOARD_SIZE = 8
const BOARD_PX   = TILE_SIZE * BOARD_SIZE   # 640
const WIN_W      = 1060
const WIN_H      = 760
const BOARD_X    = 50
const BOARD_Y    = 80

@onready var movement_manager = $MovementManager
@onready var board_tiles      = $BoardTiles

var piece_manager = null
var drills:               Array      = []
var current_drill_index:  int        = 0
var current_drill:        Dictionary = {}
var flip_board:           bool       = false

# HUD refs
var opening_label:    Label
var color_label:      Label
var stats_label:      Label
var progress_label:   Label
var hint_button:      Button
var prev_button:      Button
var next_button:      Button
var result_overlay:   ColorRect
var result_label:     Label
var drill_list_box:   VBoxContainer
var wrong_count:      int = 0
var wrong_label:      Label

func _ready() -> void:
	piece_manager = preload("res://scripts/piece_manager.gd").new()
	add_child(piece_manager)

	_build_hud()
	_draw_board()

	movement_manager.set_game_state(GameStateManager)
	movement_manager.set_piece_manager(piece_manager)
	movement_manager.set_board_root(board_tiles)
	movement_manager.set_promotion_popup($UI/PromotionPopup)

	movement_manager.puzzle_complete.connect(_on_drill_complete)
	movement_manager.wrong_move.connect(_on_wrong_move)

	_load_drills()
	if drills.size() > 0:
		_start_drill(0)

# ── Board drawing ──────────────────────────────────────────────────────────────

func _draw_board() -> void:
	board_tiles.position = Vector2(BOARD_X, BOARD_Y)

	for rank in range(BOARD_SIZE):
		for file in range(BOARD_SIZE):
			var tile       := ColorRect.new()
			tile.name      = GameStateManager.indices_to_square_name(rank, file)
			tile.size      = Vector2(TILE_SIZE, TILE_SIZE)

			# Flip visually for black drills (black at bottom)
			var display_rank = (7 - rank) if flip_board else rank
			var display_file = (7 - file) if flip_board else file
			tile.position  = Vector2(display_file * TILE_SIZE, display_rank * TILE_SIZE)

			var is_light   = (rank + file) % 2 == 0
			tile.color     = Globals.COLOR_TILE_LIGHT if is_light else Globals.COLOR_TILE_DARK

			tile.mouse_filter = Control.MOUSE_FILTER_STOP
			tile.gui_input.connect(_on_tile_input.bind(tile))
			board_tiles.add_child(tile)

func _redraw_board() -> void:
	for child in board_tiles.get_children():
		child.queue_free()
	_draw_board()

func _on_tile_input(event: InputEvent, tile: ColorRect) -> void:
	if event is InputEventMouseButton and event.pressed \
	   and event.button_index == MOUSE_BUTTON_LEFT:
		movement_manager.handle_tile_click(tile)

# ── Drill loading ──────────────────────────────────────────────────────────────

func _load_drills() -> void:
	var file = FileAccess.open("res://opening_drills.json", FileAccess.READ)
	if not file:
		opening_label.text = "opening_drills.json not found — run drill_generator.py first"
		return
	var data = JSON.parse_string(file.get_as_text())
	file.close()
	if data and data.has("drills"):
		drills = data["drills"]
		_populate_drill_list()

func _start_drill(index: int) -> void:
	if drills.is_empty():
		return

	current_drill_index = clamp(index, 0, drills.size() - 1)
	current_drill       = drills[current_drill_index]
	wrong_count         = 0

	var color  = current_drill.get("color", "white")
	var moves  = current_drill.get("moves", [])
	var stats  = current_drill.get("stats", {})
	var fen    = current_drill.get("fen", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")

	# Flip board for black drills
	var new_flip = (color == "black")
	if new_flip != flip_board:
		flip_board = new_flip
		_redraw_board()

	# Reset board state
	GameStateManager.load_from_fen(fen)
	_clear_pieces()
	piece_manager.place_pieces_from_board_state(board_tiles, GameStateManager)

	# Configure movement manager
	movement_manager.player_color = "w" if color == "white" else "b"
	movement_manager.start_puzzle(moves)

	# Update HUD
	opening_label.text  = current_drill.get("opening_name", "Unknown Opening")
	color_label.text    = "Playing as: " + color.capitalize()
	stats_label.text    = "%d games in your history  |  %.0f%% win rate" % [
		stats.get("games", 0), stats.get("win_rate", 0.0)
	]
	wrong_label.text    = ""
	result_overlay.visible = false
	prev_button.disabled   = (current_drill_index == 0)
	next_button.disabled   = (current_drill_index == drills.size() - 1)
	_update_progress()

	# Auto-play white's first move when drilling as black
	if movement_manager.player_color == "b" and moves.size() > 0:
		call_deferred("_auto_play_first_opponent_move")

func _auto_play_first_opponent_move() -> void:
	await get_tree().create_timer(0.5).timeout
	if movement_manager.solution_moves.size() > 0:
		movement_manager._apply_uci_move(movement_manager.solution_moves[0])
		movement_manager.solution_index = 1
		GameStateManager.current_turn   = "b"
	_update_progress()

func _clear_pieces() -> void:
	for tile in board_tiles.get_children():
		if tile is ColorRect:
			for child in tile.get_children():
				child.queue_free()

func _update_progress() -> void:
	var total = current_drill.get("moves", []).size()
	var idx   = movement_manager.solution_index
	# Count only the player's moves for display
	var player_moves_total = 0
	var player_moves_done  = 0
	var color = current_drill.get("color", "white")
	var player_parity = 0 if color == "white" else 1  # white moves are even indices
	for i in range(total):
		if i % 2 == player_parity:
			player_moves_total += 1
			if i < idx:
				player_moves_done += 1
	progress_label.text = "Move %d / %d" % [player_moves_done, player_moves_total]

# ── Signals ────────────────────────────────────────────────────────────────────

func _on_drill_complete() -> void:
	var color = current_drill.get("color", "white")
	if wrong_count == 0:
		result_label.text = "Perfect! No mistakes."
	else:
		result_label.text = "Complete — %d mistake%s." % [wrong_count, "s" if wrong_count > 1 else ""]
	result_overlay.visible = true

func _on_wrong_move(_tile) -> void:
	wrong_count += 1
	wrong_label.text = "Mistakes this drill: %d" % wrong_count

# ── HUD ────────────────────────────────────────────────────────────────────────

func _build_hud() -> void:
	var bg_color = Color(0.12, 0.12, 0.14)

	# Background
	var bg = ColorRect.new()
	bg.color    = bg_color
	bg.size     = Vector2(WIN_W, WIN_H)
	bg.position = Vector2.ZERO
	add_child(bg)

	# Top bar
	var top_bar = ColorRect.new()
	top_bar.color    = Color(0.08, 0.08, 0.10)
	top_bar.size     = Vector2(WIN_W, BOARD_Y - 4)
	top_bar.position = Vector2.ZERO
	add_child(top_bar)

	opening_label = _make_label("", Vector2(BOARD_X, 12), Vector2(700, 32), 16, true)
	color_label   = _make_label("", Vector2(BOARD_X, 44), Vector2(400, 24), 13)

	# Right panel
	var panel_x = BOARD_X + BOARD_PX + 24
	var panel_w = WIN_W - panel_x - 10

	_make_label("STATS", Vector2(panel_x, BOARD_Y), Vector2(panel_w, 24), 12, true)
	stats_label    = _make_label("", Vector2(panel_x, BOARD_Y + 24), Vector2(panel_w, 44), 12)

	_make_label("DRILL LIST", Vector2(panel_x, BOARD_Y + 80), Vector2(panel_w, 24), 12, true)

	var scroll = ScrollContainer.new()
	scroll.position = Vector2(panel_x, BOARD_Y + 104)
	scroll.size     = Vector2(panel_w, 340)
	add_child(scroll)

	drill_list_box = VBoxContainer.new()
	drill_list_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(drill_list_box)

	# Bottom of right panel
	var btn_y = BOARD_Y + 460
	hint_button = _make_button("Hint", Vector2(panel_x, btn_y), Vector2(panel_w, 36))
	hint_button.pressed.connect(_on_hint_pressed)

	wrong_label = _make_label("", Vector2(panel_x, btn_y + 44), Vector2(panel_w, 24), 12)
	wrong_label.modulate = Color(1, 0.4, 0.4)

	progress_label = _make_label("", Vector2(panel_x, btn_y + 74), Vector2(panel_w, 24), 13)

	# Navigation buttons
	var nav_y = BOARD_Y + BOARD_PX + 8
	prev_button = _make_button("< Prev", Vector2(BOARD_X, nav_y), Vector2(120, 36))
	next_button = _make_button("Next >", Vector2(BOARD_X + BOARD_PX - 120, nav_y), Vector2(120, 36))
	prev_button.pressed.connect(_on_prev_pressed)
	next_button.pressed.connect(_on_next_pressed)

	var restart_btn = _make_button("Restart", Vector2(BOARD_X + BOARD_PX / 2 - 60, nav_y), Vector2(120, 36))
	restart_btn.pressed.connect(_on_restart_pressed)

	# Result overlay (shown on completion)
	result_overlay         = ColorRect.new()
	result_overlay.color   = Color(0, 0, 0, 0.72)
	result_overlay.size    = Vector2(BOARD_PX, BOARD_PX)
	result_overlay.position = Vector2(BOARD_X, BOARD_Y)
	result_overlay.visible = false
	add_child(result_overlay)

	result_label = Label.new()
	result_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	result_label.vertical_alignment   = VERTICAL_ALIGNMENT_CENTER
	result_label.size                 = Vector2(BOARD_PX, BOARD_PX)
	result_label.position             = Vector2.ZERO
	result_label.add_theme_font_size_override("font_size", 28)
	result_overlay.add_child(result_label)

	var next_drill_btn = _make_button("Next Drill  >", Vector2(BOARD_PX / 2 - 80, BOARD_PX / 2 + 40), Vector2(160, 44))
	next_drill_btn.pressed.connect(_on_next_pressed)
	result_overlay.add_child(next_drill_btn)

func _populate_drill_list() -> void:
	for child in drill_list_box.get_children():
		child.queue_free()

	for i in range(drills.size()):
		var d      = drills[i]
		var name   = d.get("opening_name", "Drill %d" % (i + 1))
		var color  = d.get("color", "white")
		var stats  = d.get("stats", {})
		var wr     = stats.get("win_rate", 0.0)

		var btn         = Button.new()
		var short_name  = name if name.length() <= 34 else name.substr(0, 31) + "..."
		btn.text        = "%s (%s) — %.0f%%" % [short_name, color[0].to_upper(), wr]
		btn.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		btn.pressed.connect(_start_drill.bind(i))

		# Colour-code by win rate
		var style = StyleBoxFlat.new()
		if   wr < 35: style.bg_color = Color(0.35, 0.10, 0.10)
		elif wr < 45: style.bg_color = Color(0.35, 0.25, 0.10)
		else:         style.bg_color = Color(0.12, 0.28, 0.12)
		btn.add_theme_stylebox_override("normal", style)

		drill_list_box.add_child(btn)

# ── Button handlers ────────────────────────────────────────────────────────────

func _on_hint_pressed()    -> void: movement_manager.show_hint()
func _on_prev_pressed()    -> void: _start_drill(current_drill_index - 1)
func _on_next_pressed()    -> void: _start_drill(current_drill_index + 1)
func _on_restart_pressed() -> void: _start_drill(current_drill_index)

# ── UI helpers ─────────────────────────────────────────────────────────────────

func _make_label(text: String, pos: Vector2, size: Vector2,
				 font_size: int = 14, bold: bool = false) -> Label:
	var label = Label.new()
	label.text     = text
	label.position = pos
	label.size     = size
	label.add_theme_font_size_override("font_size", font_size)
	if bold:
		label.add_theme_color_override("font_color", Color(0.95, 0.90, 0.75))
	add_child(label)
	return label

func _make_button(text: String, pos: Vector2, size: Vector2) -> Button:
	var btn = Button.new()
	btn.text              = text
	btn.position          = pos
	btn.custom_minimum_size = size
	add_child(btn)
	return btn
