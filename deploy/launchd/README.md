# launchd schedule for the change monitor

Runs the monitor daily at 09:00 as a per-user LaunchAgent, via
`monitor-remote.sh`: it opens an ssh tunnel to the production database,
runs `uv run python -m app.monitor nsw` against it, and closes the
tunnel. It must be a LaunchAgent (not a LaunchDaemon): the fetcher drives
headed Chrome, which needs your GUI session. A failed run logs to the
error file and the next day's run recovers.

Install (from the repo root; substitute your server and the production
DB password):

```bash
sed -e "s|__REPO_DIR__|$(pwd)|g" \
    -e "s|__HOME__|$HOME|g" \
    -e "s|__SERVER__|deploy@YOUR.SERVER.IP|g" \
    -e "s|__REMOTE_DATABASE_URL__|postgresql+asyncpg://postgres:YOUR-DB-PASSWORD@localhost:15433/lease_compliance|g" \
    deploy/launchd/com.lease-monitor.plist \
    > ~/Library/LaunchAgents/com.lease-monitor.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lease-monitor.plist
```

The monitor now writes the production database through the tunnel; the
fetch still needs your GUI session for headed Chrome.

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
