#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Per-project board-sync watcher — install/uninstall.
#
# ADT's board only stays in sync with GitHub Issues if SOMETHING runs
# `adt_watch.py --once` on an interval. Before this, the installer rendered the
# board exactly once and printed a "run this yourself" hint, so every project
# started with a dead sync — a board move never reached GitHub until a human
# manually ran the watch. A watcher must exist for EVERY project ADT runs in;
# the installer now creates one, and the uninstaller removes it.
#
# It calls `adt_watch.py --root <project> --once` directly (the same config-aware
# render+sync path the installer already invokes once), so there is no per-project
# wrapper script to author or keep in step. --once on an interval (not a resident
# loop) means a hung/crashed pass is replaced on the next tick rather than wedging
# a permanently-resident process.
#
# Platform: macOS → launchd user agent; Linux → systemd --user timer. Anything
# else → warn + skip (the manual `adt watch` hint still applies). All functions
# are idempotent. On macOS a reinstall leaves an identical, loaded agent alone
# and replaces one that differs; on Linux it always replaces. Re-uninstalling is
# a no-op.
#
# Usage:
#   install_watcher   <ADT_DIR> <project_name> <project_path> [interval_seconds]
#   uninstall_watcher <project_name> <project_path>

set -euo pipefail

# A launchd/systemd-safe id from the project name: lowercase, non-alnum → '-'.
_watcher_slug() { echo "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-' | sed 's/-\{2,\}/-/g; s/^-//; s/-$//'; }
_watcher_label() { echo "com.adt.$(_watcher_slug "$1").watch"; }

# Compose a PATH that includes wherever python3 + gh actually live, on top of the
# minimal set launchd/systemd give a user agent (gh keychain auth needs gh on PATH).
_watcher_path() {
  local out="" d
  # Tool dirs first (so gh/python3 resolve), then the standard minimal set;
  # dedup against everything already added.
  for d in "$(command -v python3 2>/dev/null)" "$(command -v gh 2>/dev/null)" "$(command -v git 2>/dev/null)"; do
    [[ -n "$d" ]] && d="$(dirname "$d")" || continue
    case ":$out:" in *":$d:"*) ;; *) out="${out:+$out:}$d";; esac
  done
  for d in /usr/local/bin /usr/bin /bin /sbin /usr/sbin; do
    case ":$out:" in *":$d:"*) ;; *) out="${out:+$out:}$d";; esac
  done
  echo "$out"
}

# ADT-066 defects 5+6: remove ANY board-sync watcher targeting this project,
# not just ADT's own derived label. A project that once ran a differently-named
# watcher (e.g. a consumer's com.<project>.adt-watch) would otherwise survive a
# reinstall and run alongside com.adt.<slug>.watch — two daemons rendering the
# same board every 60s. Match structurally: an agent counts as "ours to remove"
# only if its definition invokes adt_watch.py AND targets this project via
# `--root <path>` (or the cache dir under it). Returns the count still matching
# after removal so the caller can assert zero remain.
# An optional launchd label is skipped (macOS only): install passes its own
# label so an unchanged agent can be kept rather than removed and re-created.
#   Usage: _remove_watchers_for_path <project_path> [skip_label]  → echoes leftover count
_remove_watchers_for_path() {
  local path="$1" skip="${2:-}" left=0 f
  case "$(uname -s)" in
    Darwin)
      local dir="$HOME/Library/LaunchAgents"
      [[ -d "$dir" ]] || { echo 0; return 0; }
      for f in "$dir"/*.plist; do
        [[ -f "$f" ]] || continue
        [[ -n "$skip" && "$(basename "$f" .plist)" == "$skip" ]] && continue
        # Must be an adt_watch.py agent for THIS project's path.
        grep -q 'adt_watch\.py' "$f" 2>/dev/null || continue
        grep -Fq -- "$path" "$f" 2>/dev/null || continue
        local label; label="$(basename "$f" .plist)"
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null \
          || launchctl unload "$f" 2>/dev/null || true
        rm -f "$f"
        echo "  [watcher] removed launchd watcher: $label (targets $path)" >&2
      done
      # Re-scan: anything still on disk pointing here is a leftover.
      for f in "$dir"/*.plist; do
        [[ -f "$f" ]] || continue
        [[ -n "$skip" && "$(basename "$f" .plist)" == "$skip" ]] && continue
        grep -q 'adt_watch\.py' "$f" 2>/dev/null && grep -Fq -- "$path" "$f" 2>/dev/null && left=$((left+1))
      done
      ;;
    Linux)
      local dir="$HOME/.config/systemd/user"
      [[ -d "$dir" ]] || { echo 0; return 0; }
      command -v systemctl >/dev/null 2>&1 || { echo 0; return 0; }
      for f in "$dir"/*.service; do
        [[ -f "$f" ]] || continue
        grep -q 'adt_watch\.py' "$f" 2>/dev/null || continue
        grep -Fq -- "$path" "$f" 2>/dev/null || continue
        local unit; unit="$(basename "$f" .service)"
        systemctl --user disable --now "$unit.timer" 2>/dev/null || true
        systemctl --user disable --now "$unit.service" 2>/dev/null || true
        rm -f "$dir/$unit.timer" "$dir/$unit.service"
        echo "  [watcher] removed systemd --user watcher: $unit (targets $path)" >&2
      done
      systemctl --user daemon-reload 2>/dev/null || true
      for f in "$dir"/*.service; do
        [[ -f "$f" ]] || continue
        grep -q 'adt_watch\.py' "$f" 2>/dev/null && grep -Fq -- "$path" "$f" 2>/dev/null && left=$((left+1))
      done
      ;;
  esac
  echo "$left"
}

install_watcher() {
  local adt_dir="${1:?usage: install_watcher <ADT_DIR> <name> <path> [interval]}"
  local name="${2:?need project name}"
  local path="${3:?need project path}"
  local interval="${4:-60}"
  local py; py="$(command -v python3 || echo /usr/bin/python3)"
  local watch="$adt_dir/tools/adt_watch.py"
  local logdir="$path/.adt/state"
  mkdir -p "$logdir"
  local log="$logdir/adt-watch.log"

  # ADT-066 defect 5: each branch first sweeps away ANY watcher already targeting
  # this project (including a foreign-named one), so a reinstall can't leave two
  # daemons racing on the same board.
  case "$(uname -s)" in
    Darwin)
      local label; label="$(_watcher_label "$name")"
      local plist="$HOME/Library/LaunchAgents/$label.plist"
      mkdir -p "$HOME/Library/LaunchAgents"
      # Our own label is skipped here and handled below.
      _remove_watchers_for_path "$path" "$label" >/dev/null
      # Rendered into a variable, not a file: a temp .plist in LaunchAgents would
      # be matched by the sweep's glob and set off a macOS background-item scan.
      # read -d '' (not $(cat <<EOF)) because bash 3.2 misparses heredocs inside
      # command substitution. It returns 1 at end of input, hence || true.
      local xml
      IFS= read -r -d '' xml <<EOF || true
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <!-- ADT board sync. Every ${interval}s it reconciles the local ticket cache
         to GitHub Issues + re-renders the kanban. Runs as a user agent so the gh
         keychain credential resolves. Read-only to the code; touches only gh +
         the local cache. Installed by adt-install.sh.

         RESIDENT since AO-006. This was --once on a StartInterval, deliberately:
         a hung pass was replaced on the next tick. The board's stop/start button
         POSTs to this process, and a --once tick is gone 59 seconds out of 60,
         so there was nothing alive to take the click. Three earlier designs of
         that ticket tried to route around this and each one failed on it.

         What it costs, stated rather than discovered later: a wedged pass now
         stays wedged. adt_sync's pass lock keeps that safe rather than
         corrupting, but it can stall quietly and nothing watches for it yet.
         KeepAlive restarts the agent if the process dies, which is a different
         failure from a pass that hangs.

         The listener binds 127.0.0.1 and serves two routes: the rendered board,
         and the toggle. See _serve_board in tools/adt_watch.py. -->
    <key>ProgramArguments</key>
    <array>
        <string>$py</string>
        <string>$watch</string>
        <string>--root</string>
        <string>$path</string>
        <string>--interval</string>
        <string>$interval</string>
    </array>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>$path</string>
    <key>StandardOutPath</key>
    <string>$log</string>
    <key>StandardErrorPath</key>
    <string>$log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$(_watcher_path)</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
</dict>
</plist>
EOF
      # ADT-372: leave an identical, loaded agent alone. When python3 is unsigned
      # (an Intel build), macOS cannot match a rewritten plist to the item it
      # already approved, so every rewrite registers a new background item and
      # shows the "python3 can run in the background" alert, even with no change.
      if [[ -f "$plist" ]] && cmp -s "$plist" <(printf '%s' "$xml") \
          && launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
        echo "  launchd watcher unchanged: $label"
        return 0
      fi
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || launchctl unload "$plist" 2>/dev/null || true
      printf '%s' "$xml" > "$plist"
      launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || launchctl load "$plist" 2>/dev/null || true
      echo "  installed launchd watcher: $label (every ${interval}s)"
      return 0
      ;;
    Linux)
      _remove_watchers_for_path "$path" >/dev/null
      if ! command -v systemctl >/dev/null 2>&1; then
        echo "  (no systemctl — skipping watcher; keep the board in sync manually: python3 $watch --root $path)" >&2
        return 0
      fi
      local slug; slug="$(_watcher_slug "$name")"
      local unit="adt-watch-$slug"
      local ud="$HOME/.config/systemd/user"
      mkdir -p "$ud"
      cat > "$ud/$unit.service" <<EOF
[Unit]
Description=ADT board sync for $name (cache <-> GitHub Issues + kanban render)

[Service]
Type=oneshot
WorkingDirectory=$path
Environment=PATH=$(_watcher_path)
ExecStart=$py $watch --root $path --once
EOF
      cat > "$ud/$unit.timer" <<EOF
[Unit]
Description=Run ADT board sync for $name every ${interval}s

[Timer]
OnBootSec=${interval}
OnUnitActiveSec=${interval}
AccuracySec=5

[Install]
WantedBy=timers.target
EOF
      systemctl --user daemon-reload 2>/dev/null || true
      systemctl --user enable --now "$unit.timer" 2>/dev/null \
        && echo "  installed systemd --user watcher: $unit.timer (every ${interval}s)" \
        || echo "  (systemd --user not active — enable later: systemctl --user enable --now $unit.timer)" >&2
      return 0
      ;;
    *)
      echo "  (unsupported OS '$(uname -s)' for auto-watcher — keep the board in sync manually: python3 $watch --root $path)" >&2
      return 0
      ;;
  esac
}

uninstall_watcher() {
  local name="${1:?usage: uninstall_watcher <name> <path>}"
  local path="${2:?need project path}"

  # First, the systemd timer for our derived label — _remove_watchers_for_path
  # disables the .timer too, but the named-unit path keeps the historical label
  # in the uninstall log and handles a .timer with no matching .service text.
  if [[ "$(uname -s)" == "Linux" ]] && command -v systemctl >/dev/null 2>&1; then
    local slug; slug="$(_watcher_slug "$name")"
    local unit="adt-watch-$slug"
    local ud="$HOME/.config/systemd/user"
    if [[ -f "$ud/$unit.timer" || -f "$ud/$unit.service" ]]; then
      systemctl --user disable --now "$unit.timer" 2>/dev/null || true
      rm -f "$ud/$unit.timer" "$ud/$unit.service"
      systemctl --user daemon-reload 2>/dev/null || true
      echo "  [uninstall] removed systemd --user watcher: $unit"
    fi
  fi

  # ADT-066 defects 5+6: remove ANY watcher targeting this project (foreign-named
  # included), then ASSERT none remains pointing here. A leftover daemon after
  # uninstall is the bug we're closing — surface it loudly rather than leave it
  # rendering silently.
  local left; left="$(_remove_watchers_for_path "$path")"
  if [[ "${left:-0}" -gt 0 ]]; then
    echo "  [uninstall] WARN: $left watcher(s) still target $path after removal — inspect ~/Library/LaunchAgents (or ~/.config/systemd/user) by hand." >&2
  else
    echo "  [uninstall] verified: no board-sync watcher targets $path"
  fi
}
