# launchd schedule for the change monitor

Runs `uv run python -m app.monitor nsw` daily at 09:00 as a per-user
LaunchAgent. It must be a LaunchAgent (not a LaunchDaemon): the fetcher
drives headed Chrome, which needs your GUI session. The Postgres store
must be running at fire time; a failed run logs to the error file and the
next day's run recovers.

Install (from the repo root):

```bash
sed -e "s|__REPO_DIR__|$(pwd)|g" \
    -e "s|__UV__|$(which uv)|g" \
    -e "s|__HOME__|$HOME|g" \
    deploy/launchd/com.lease-monitor.plist \
    > ~/Library/LaunchAgents/com.lease-monitor.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lease-monitor.plist
```

Trigger a run now:

```bash
launchctl kickstart gui/$(id -u)/com.lease-monitor
```

Logs: `~/Library/Logs/lease-monitor.log` and `lease-monitor.err.log`.

Uninstall:

```bash
launchctl bootout gui/$(id -u)/com.lease-monitor
rm ~/Library/LaunchAgents/com.lease-monitor.plist
```

To change the schedule, edit `StartCalendarInterval` in the installed
plist, then `bootout` and `bootstrap` again.
