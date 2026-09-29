---
name: "mcp_config-cli"
description: "CLI for the mcp_config MCP server. Call tools, list resources, and get prompts."
---

# mcp_config CLI

## Tool Commands

### list_windows

Enumerate visible windows: title, window handle, and rect. Pass a filter (partial title) to narrow.

```bash
uv run --with fastmcp python cli.py call-tool list_windows --filter <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--filter` | string | no |  |

### inspect_window

Inspect the UIA control tree of a window (by title, partial match). Returns structured controls with their name, control_type, automation_id, class, and rect. depth limits recursion. This is the structured 'eyes' — no vision model needed.

```bash
uv run --with fastmcp python cli.py call-tool inspect_window --title <value> --depth <value> --max-controls <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--title` | string | yes |  |
| `--depth` | integer | no |  |
| `--max-controls` | integer | no |  |

### click_element

Click a UI element in a window by name or automation_id. UIA-first: finds the control in the accessibility tree and clicks it — no pixel guessing. Pass title (window, partial) and either name or automation_id. Optional button (left/right/double).

```bash
uv run --with fastmcp python cli.py call-tool click_element --title <value> --name <value> --automation-id <value> --button <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--title` | string | yes |  |
| `--name` | string | no |  |
| `--automation-id` | string | no |  |
| `--button` | string | no |  |

### type_text

Type text into the active/focused control of a window (by title). Text is sent literally (paths, punctuation safe).

```bash
uv run --with fastmcp python cli.py call-tool type_text --title <value> --text <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--title` | string | yes |  |
| `--text` | string | yes |  |

### navigate_to

Navigate a File Explorer window to a folder path. Uses the address bar Edit control directly (set text + Enter) — deterministic, no mouse/focus hunting. Pass title (window, partial) and path (e.g. C:\Users\User\Pictures).

```bash
uv run --with fastmcp python cli.py call-tool navigate_to --title <value> --path <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--title` | string | yes |  |
| `--path` | string | yes |  |

### press_key

Send keyboard hotkeys to the active window. Accepts pywinauto syntax ('^c' for ctrl+c, '%{TAB}' for alt+tab, '{ENTER}') OR plain names ('ctrl+c', 'ctrl+x', 'ctrl+v', 'alt+tab', 'enter', 'esc'). Prefer the plain-name form — the caret '^' can be eaten by the shell layer.

```bash
uv run --with fastmcp python cli.py call-tool press_key --keys <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--keys` | string | yes |  |

### mouse_click

Move the mouse to absolute screen coordinates and click. Uses SendInput (same low-level input as OpenAI Computer Use). Coordinates are in native screen pixels: x,y from the top-left of the primary monitor. Optional button: left/right/double. Refuses to click ELEVATED windows (UIPI silently blocks them — reports honestly instead).

```bash
uv run --with fastmcp python cli.py call-tool mouse_click --x <value> --y <value> --button <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--x` | integer | yes |  |
| `--y` | integer | yes |  |
| `--button` | string | no |  |

### mouse_position

Return the current cursor position in screen pixels (GetCursorPos). Cheap verification for where the mouse actually is.

```bash
uv run --with fastmcp python cli.py call-tool mouse_position
```

### scroll

Scroll the mouse wheel at the current cursor position. Positive clicks scroll up (away from user), negative scrolls down. e.g. scroll(3) scrolls up 3 notches, scroll(-5) scrolls down 5.

```bash
uv run --with fastmcp python cli.py call-tool scroll --clicks <value> --x <value> --y <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--clicks` | integer | yes |  |
| `--x` | integer | no |  |
| `--y` | integer | no |  |

### drag

Drag from (x1,y1) to (x2,y2): press the button at the start, move through the path, release at the end. Coordinates are virtual-desktop (multi-monitor safe). Optional button: left/right.

```bash
uv run --with fastmcp python cli.py call-tool drag --x1 <value> --y1 <value> --x2 <value> --y2 <value> --button <value> --steps <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--x1` | integer | yes |  |
| `--y1` | integer | yes |  |
| `--x2` | integer | yes |  |
| `--y2` | integer | yes |  |
| `--button` | string | no |  |
| `--steps` | integer | no |  |

### get_clipboard

Read the current clipboard text. Use to verify a copy/cut actually captured content before pasting.

```bash
uv run --with fastmcp python cli.py call-tool get_clipboard
```

### set_clipboard

Set the clipboard to text. Use before a deterministic paste when you need known content on the clipboard.

```bash
uv run --with fastmcp python cli.py call-tool set_clipboard --text <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--text` | string | yes |  |

### move_file

Move a file to a destination folder deterministically via copy-then-verify-then-delete — never cut+paste. Copying first means the source survives any failure; the original is only deleted after the copy is verified to exist. Pass source path and destination folder.

```bash
uv run --with fastmcp python cli.py call-tool move_file --source <value> --destination <value> --copy
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--source` | string | yes |  |
| `--destination` | string | yes |  |
| `--copy` | boolean | no |  |

### run_command

Run a PowerShell command and capture output. Use for anything the shell does faster than clicking (create folder, list files, run a script, query services). This is the UFO² hybrid principle: shell beats GUI clicks when it can. For destructive commands (delete, kill, format, registry writes) use run_command_guarded which requires your approval first.

```bash
uv run --with fastmcp python cli.py call-tool run_command --command <value> --timeout <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--command` | string | yes |  |
| `--timeout` | integer | no |  |

### launch_app

Launch a program by path or a start command (app name, file, URL). Uses os.startfile for double-click-equivalent launch.

```bash
uv run --with fastmcp python cli.py call-tool launch_app --target <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--target` | string | yes |  |

### focus_window

Bring a window to the foreground by title (partial match). Does not move the mouse.

```bash
uv run --with fastmcp python cli.py call-tool focus_window --title <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--title` | string | yes |  |

### screenshot

Capture the screen (full, window, or region) via the canonical Zoro screenshot script. Returns path for vision analysis.

```bash
uv run --with fastmcp python cli.py call-tool screenshot --mode <value> --target <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--mode` | string | no |  |
| `--target` | string | no |  |

### find_on_screen

Vision-driven element location: screenshot the screen (or a window), ask the local VLM where a described element is, and return approximate pixel coordinates. Use when UIA can't identify the control (canvas apps, games, custom UI). Pass what='the red Submit button' or similar. This is the UI-TARS-style fallback.

```bash
uv run --with fastmcp python cli.py call-tool find_on_screen --what <value> --window <value> --mode <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--what` | string | yes |  |
| `--window` | string | no |  |
| `--mode` | string | no |  |

### verify_state

Vision-driven verification: screenshot the screen/window and ask the local VLM whether a condition is true (e.g. 'is there a green checkmark?', 'did a dialog open?', 'is the app running?'). Returns yes/no + explanation. Use to close the act→verify loop.

```bash
uv run --with fastmcp python cli.py call-tool verify_state --condition <value> --window <value> --mode <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--condition` | string | yes |  |
| `--window` | string | no |  |
| `--mode` | string | no |  |

### window_manager_ui

Open the interactive Window Manager app: a searchable/sortable window table where you can FOCUS any window by clicking a button (FastMCP v3 FastMCPApp with UI→server callbacks).

```bash
uv run --with fastmcp python cli.py call-tool window_manager_ui
```

### search_tools

Search for tools using natural language.

Returns matching tool definitions ranked by relevance,
in the same format as list_tools.

```bash
uv run --with fastmcp python cli.py call-tool search_tools --query <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--query` | string | yes | Natural language query to search for tools |

### call_tool

Call a tool by name with the given arguments.

Use this to execute tools discovered via search_tools.

```bash
uv run --with fastmcp python cli.py call-tool call_tool --name <value> --arguments <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--name` | string | yes | The name of the tool to call |
| `--arguments` | string | no | Arguments to pass to the tool (JSON string) |

## Utility Commands

```bash
uv run --with fastmcp python cli.py list-tools
uv run --with fastmcp python cli.py list-resources
uv run --with fastmcp python cli.py read-resource <uri>
uv run --with fastmcp python cli.py list-prompts
uv run --with fastmcp python cli.py get-prompt <name> [key=value ...]
```
