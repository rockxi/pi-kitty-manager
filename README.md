<div align="center">

# pi-kitty-manager

**All your pi agents in one place.**

Switch between conversations instead of hunting through terminal tabs.

[![GitHub stars](https://img.shields.io/github/stars/rockxi/pi-kitty-manager?style=flat-square&label=stars&color=E9B86C)](https://github.com/rockxi/pi-kitty-manager/stargazers)
[![GitHub last commit](https://img.shields.io/github/last-commit/rockxi/pi-kitty-manager?style=flat-square&color=8BCABF)](https://github.com/rockxi/pi-kitty-manager/commits)
[![Python](https://img.shields.io/badge/Python-3.11%2B-8BCABF?style=flat-square)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/tested-macOS-8BCABF?style=flat-square)](#compatibility)

**English** · [Русский](README.ru.md)

[Installation](#installation) · [Usage](#usage) · [Troubleshooting](#troubleshooting) · [How it works](#how-it-works)

</div>

---

### Install with a pi agent

Copy and send this to your pi agent:

```text
Please install and configure https://github.com/rockxi/pi-kitty-manager for me. Follow the repository's installation instructions and explain any changes you make.
```

---

Run [pi](https://github.com/earendil-works/pi) agents in different [kitty](https://sw.kovidgoyal.net/kitty/) tabs and panes. Press a keyboard shortcut, find a conversation, and jump straight to it — even if it is in another kitty window.

The current terminal interface is in Russian. The following example illustrates its layout; actual labels are shown in Russian:

```text
 Pi agents  ·  4 agents  ·  2 working
 Search: _

 ~/projects/backend
 › Working / tools  Fix authentication    [tab 2 · pane 5]
   Idle             Refactor API           [tab 3 · pane 7]

 ~/projects/frontend
   Working          Registration form      [tab 4 · pane 8]
   Idle             Responsive layout      [tab 5 · pane 9]

 ↑↓ select   Ctrl+D/U half-page   Enter switch   Esc close
```

### At a glance

- **Only pi agents.** Shell tabs and other programs do not clutter the list.
- **Folders and conversations.** Agents are grouped by working directory, with their pi session names displayed.
- **Live status.** See which agents are working or idle; status is indicated by both text and color.
- **Search and switch.** Search by folder, conversation name, model, or tab. Press Enter to focus the pane.
- **Saved history.** Cycle active, history and hidden views with `Ctrl+T`; select a closed session to resume it in a new kitty tab.
- **Starts with kitty.** The daemon starts in the background without an extra tab; multiple windows reuse the same daemon.
- **Local by design.** No web server, TCP listener, or model requests. History is read locally; the first user message may be shown for unnamed sessions.

## Installation

### Requirements

- macOS, [kitty](https://sw.kovidgoyal.net/kitty/), and [pi](https://github.com/earendil-works/pi).
- Python **3.11+** for the CLI and manual startup. The daemon and interface have no third-party Python runtime dependencies.
- Tested with kitty **0.45.0** and pi **1.4.0**. See [Compatibility](#compatibility).

### 1. Clone the repository

```bash
git clone https://github.com/rockxi/pi-kitty-manager.git ~/pi-kitty-manager
cd ~/pi-kitty-manager
```

You can choose another location, but keep the repository there after installation: kitty and pi use absolute paths to its files.

### 2. Install the pi extension

```bash
pi install "$(pwd)/pi-extension"
```

Run `/reload` in each **already running** pi session. New instances will load the extension automatically. The extension provides accurate activity statuses and conversation names.

### 3. Configure kitty

Open `~/.config/kitty/kitty.conf` and add these lines, replacing the placeholder with the **absolute path to your clone**:

```conf
watcher /ABSOLUTE/PATH/pi-kitty-manager/kitty/autostart.py
map ctrl+shift+p kitten /ABSOLUTE/PATH/pi-kitty-manager/kitty/agents.py
```

To generate the lines with your actual path, run this from the repository root:

```bash
printf 'watcher "%s/kitty/autostart.py"\nmap ctrl+shift+p kitten "%s/kitty/agents.py"\n' "$(pwd -P)" "$(pwd -P)"
```

Copy the output into `kitty.conf`. You can choose a different, unused shortcut instead of `ctrl+shift+p`.

**Kitty Remote Control over a local Unix socket is required.** If it is already configured, you do not need to change your existing setup. For a new configuration on macOS:

```bash
mkdir -p "$HOME/Library/Caches/pi-kitty/run"
chmod 700 "$HOME/Library/Caches/pi-kitty/run"
```

Add to `kitty.conf`:

```conf
allow_remote_control socket-only
listen_on unix:${HOME}/Library/Caches/pi-kitty/run/kitty-{kitty_pid}.sock
```

> **Security:** `socket-only` allows all kitty Remote Control commands over the socket, not just focus commands. The private directory restricts access by other users, but does not isolate processes running under your own UID. The manager itself uses `ls` and `focus-window`. Do not replace an existing configuration without checking whether other tools depend on it.

After changing `listen_on`, **restart kitty** — reloading its configuration is not enough. The watcher will start the daemon automatically when kitty starts.

### 4. Check the installation

Open several pi agents in kitty and press **Ctrl+Shift+P**. Alternatively, run the following from the repository directory:

```bash
./bin/pi-kitty daemon ensure  # start the daemon for an already open kitty, if needed
./bin/pi-kitty doctor
./bin/pi-kitty list
```

The [detailed installation guide](docs/installation.md) covers an optional LaunchAgent, advanced configuration, and removal; it is currently in Russian.

## Interface language

The default is Russian for compatibility with existing installations. To use English, add this **top-level** setting to `~/.config/pi-kitty/config.toml` (or `$XDG_CONFIG_HOME/pi-kitty/config.toml`):

```toml
language = "en" # use "ru" for Russian
```

Create the `pi-kitty` configuration directory if needed. Place `language` before any `[kitty]` section; preserve existing settings. Reopen the panel to apply it. Alternatively set `PI_KITTY_LANG=en` in the kitty process environment; the environment variable overrides the config. The CLI uses the same setting.

## Usage

| Action | Key |
| --- | --- |
| Open the list | `Ctrl+Shift+P`, or your configured kitty shortcut |
| Find an agent | Press `/` and type a conversation name, folder, or model; `Esc` leaves search |
| Select | `j` / `k`, `↑` / `↓`, or `Tab` / `Shift+Tab` |
| Switch | `Enter` |
| Close without switching | `Esc` |
| Move half a page down / up | `Ctrl+D` / `Ctrl+U` |
| Clear search | `Ctrl+L` |
| Cycle active / history / hidden views | `Ctrl+T` |
| Hide or restore one closed session | `dd` |
| Select and hide or restore a range of closed sessions | `V`, move with `j` / `k`, then `d` |
| Cancel visual selection | `Esc` |
| Undo hiding or restoring | `u` (while the panel remains open) |
| Refresh | `Ctrl+R` |

Hidden sessions are not deleted: their file paths are stored locally in `~/.config/pi-kitty/hidden-sessions.json` (or `$XDG_CONFIG_HOME/pi-kitty/hidden-sessions.json`). In the hidden view, `dd` or `V` → `d` restores them. Active chats are never included in the selection. Press `u` repeatedly to undo up to 100 visibility changes in the current panel session; the undo history is cleared when the panel closes. Search starts only with `/`; while searching, `d`, `j`, `k`, and `u` are ordinary characters.

Set a **conversation name** in pi itself:

```text
/name Fix authentication
```

Or start a session with `pi --name "Fix authentication"`. If a session has no name, the manager shows its first message when available, otherwise “Untitled”; it does not generate a title automatically.

## Troubleshooting

With `language = "en"`, the panel uses English labels:

| What you see | What to do |
| --- | --- |
| “Without pi extension” / “Unknown” | Make sure the pi extension is installed and run `/reload` in that pi process. Without the extension, process discovery is limited. |
| “Untitled” | Name the session in pi with `/name …`. |
| Daemon unavailable | Run `./bin/pi-kitty daemon ensure`, then `./bin/pi-kitty doctor`. Check the `watcher` line in kitty.conf. |
| No available kitty instances / «Нет панели» (no pane) | Check `listen_on`, Remote Control, and whether kitty was restarted after changing its socket. |
| Shortcut does not work | Check the absolute path, shortcut conflicts, and whether kitty.conf was loaded. |

For more scenarios and configuration details, see the [installation guide in Russian](docs/installation.md#7-диагностика).

## How it works

```text
pi + extension ── status and name ──┐
                                   ├── Unix socket ── daemon ── kitty Remote Control
kitty watcher ── automatic start ───┘                    │
                                                 kitten: list and search
```

The daemon keeps an in-memory registry of active agents and checks their processes and pane locations. The pi extension reports activity and the current session name. The kitten opens a list over the terminal and focuses the selected pane after the overlay closes.

## Compatibility

The first target platform is **macOS**, tested with kitty 0.45.0 and pi 1.4.0. Linux has basic path and Unix-socket support, but has not yet been fully live-tested.

Saved history is read locally from pi's default session directory and, when set, `PI_CODING_AGENT_SESSION_DIR`. Additional directories can be supplied via the `PI_KITTY_SESSION_DIRS` environment variable (separated by `:` on macOS/Linux). Sessions without a saved file (`--no-session`) cannot be resumed. If an older pi extension has not been reloaded, matching an open session is best-effort.

SSH and internal tmux/screen panes are not supported. Without the extension, discovery of arbitrary wrappers and background processes is not guaranteed, and accurate statuses and names are unavailable. Switching to other macOS Spaces and minimized windows has not been separately verified. The [implementation status](docs/implementation-status.md) provides further details in Russian.

## Development and tests

```bash
python3 -m unittest discover -s tests -v
node --test tests/extension.test.cjs
node scripts/check-extension-types.cjs
```

The Node checks use the installed npm package of pi; type-checking also requires `tsc`. Set `PI_PACKAGE_ROOT` if pi is installed in a non-standard location.

The optional GUI tests create **separate temporary kitty windows**, do not change your configuration, and do not call a model:

```bash
python3 tests/live_smoke.py
python3 tests/live_autostart.py
```

[Technical design (Russian)](docs/technical-design.md) · [Implementation status (Russian)](docs/implementation-status.md) · [Report an issue](https://github.com/rockxi/pi-kitty-manager/issues)
