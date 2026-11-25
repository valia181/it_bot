# Corporate IT Support Bot

## Manual Telegram bot control (Windows)

Run one command from the repository root to start or stop the bot:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bot.ps1 start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bot.ps1 stop
```

`start` launches one local bot process in the background. `stop` terminates
only this project's bot process; `status` checks whether it is running.
Commands can also be run as `.\scripts\bot.ps1 start` and
`.\scripts\bot.ps1 stop` if the PowerShell execution policy allows scripts.
Bot output is written to the ignored `.bot\` directory.

A Telegram `getUpdates` conflict means a different client is polling with the
same token. The local stop command cannot stop a poller running on another
machine; stop that instance or revoke the token with BotFather and update the
local `.env`.