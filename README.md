# Discord Giveaway Bot

A feature-rich Discord bot for managing giveaways with automatic winner selection, vouch tracking, and leaderboard functionality.

## Features

- 🎉 **Interactive Giveaways**: Create giveaways with Join/Leave buttons
- ⏰ **Automatic Closing**: Giveaways automatically end at their deadline
- 🏆 **Winner Tracking**: Track wins per user and server-wide leaderboards
- 📝 **Vouch System**: Users must vouch for giveaways they've won before joining new ones
- 🎲 **Reroll Support**: Reroll winners from existing entrants
- 📊 **Statistics**: View user wins, vouches, and server leaderboards
- ⚙️ **Manual Entries**: Record giveaways that weren't hosted by the bot
- 🔧 **Configurable**: Set custom vouch channels per server

## Requirements

- Python 3.10 or higher
- discord.py 2.x
- aiosqlite
- python-dotenv

## Installation

1. **Clone or download this repository**

2. **Install dependencies:**
   ```bash
   pip install -U discord.py aiosqlite python-dotenv
   ```

3. **Create a `.env` file** in the project root:
   ```
   TOKEN=your_discord_bot_token_here
   ```

4. **Get your Discord bot token:**
   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Create a new application or select an existing one
   - Go to the "Bot" section
   - Click "Reset Token" or copy your existing token
   - Paste it into your `.env` file

5. **Invite your bot to your server:**
   - In the Discord Developer Portal, go to "OAuth2" → "URL Generator"
   - Select scopes: `bot` and `applications.commands`
   - Select bot permissions: `Manage Server`, `Send Messages`, `Read Message History`, `Embed Links`, `Use External Emojis`
   - Copy the generated URL and open it in your browser to invite the bot

## Running the Bot

```bash
python bot.py
```

The bot will:
- Initialize the SQLite database (`giveaways.db`)
- Sync slash commands with Discord
- Start the background scheduler for automatic giveaway closing

## Commands

All commands use the `/gw` prefix.

### Giveaway Management

- `/gw start duration:<time> winners:<count> prize:<text>` - Start a new giveaway
  - Duration format: `10s`, `10m`, `2h`, `1d` (minimum 10 seconds)
  - Winners: 1-50
  - Requires: Manage Server permission

- `/gw end giveaway_id:<id>` - End a running giveaway early
  - Requires: Manage Server permission

- `/gw reroll giveaway_id:<id> [count:<number>] [target_user:<user>]` - Reroll winners
  - Excludes all previous winners from the new pool
  - Requires: Manage Server permission

- `/gw list` - List all giveaways (active and past) in the server

- `/gw delete giveaway_id:<id>` - Delete a giveaway from the database
  - Requires: Manage Server permission

### Statistics & Tracking

- `/gw wins [user:<user>]` - Show how many giveaways a user has won
  - Shows list of all won giveaways

- `/gw leaderboard` - Display top giveaway winners with win counts and vouches
  - Paginated view for large leaderboards

- `/gw vouches [user:<user>]` - Show vouches for a user
  - Lists all giveaways the user has vouched for

### Manual Entry & Adjustments

- `/gw manual prize:<text> winner:<user>` - Record a manual giveaway
  - Useful for giveaways not hosted by the bot
  - Requires: Manage Server permission

- `/gw adjustwins giveaway_id:<id> user:<user> action:<add|remove>` - Adjust wins for a user
  - Requires: Manage Server permission

### Vouch System

- `/gw vouch giveaway_id:<id>` - Record a vouch for a giveaway you won
  - You must have won the giveaway to vouch for it

- `/gw addvouch user:<user> giveaway_id:<id>` - Mod-only: Add a vouch for a user
  - Requires: Manage Server permission

- `/gw removevouch user:<user> giveaway_id:<id>` - Mod-only: Remove a vouch
  - Also blocks future vouches for that giveaway
  - Requires: Manage Server permission

### Configuration

- `/gw config vouch_channel set channel:<channel>` - Set the vouch channel
  - Users can type "vouch" in this channel to automatically vouch
  - Requires: Manage Server permission

- `/gw config vouch_channel show` - Show the configured vouch channel

- `/gw help` - Display all available commands

## How It Works

### Giveaway Flow

1. **Starting a Giveaway**: Use `/gw start` to create a giveaway with a Join button
2. **Entering**: Users click the Join button to enter
3. **Automatic Closing**: The bot checks every 5 seconds and closes giveaways at their deadline
4. **Winner Selection**: Winners are randomly selected from all entrants
5. **Announcement**: Winners are announced in the giveaway channel

### Vouch System

The bot enforces a vouch system to ensure users complete their previous giveaways:

- Users must have **vouches ≥ wins** to join new giveaways
- When a user wins, they must vouch for that giveaway before joining others
- Vouches can be recorded:
  - Automatically by typing "vouch" in the configured vouch channel
  - Manually using `/gw vouch`
  - By moderators using `/gw addvouch`

### Database Structure

The bot uses SQLite with the following tables:
- `giveaways` - Stores giveaway information
- `entrants` - Tracks who entered each giveaway
- `winners` - Records winners for each giveaway
- `win_counts` - Aggregated win counts per user per server
- `vouches` - Records vouches for giveaways
- `vouch_blocks` - Blocks specific vouches (moderator action)
- `guild_config` - Server-specific configuration

## Permissions Required

The bot needs the following permissions:
- **Manage Server** - For giveaway management commands
- **Send Messages** - To post giveaway messages
- **Read Message History** - To read messages in vouch channels
- **Embed Links** - To display rich giveaway embeds
- **Use External Emojis** - For emoji in embeds

## Troubleshooting

### Bot doesn't respond to commands
- Make sure slash commands are synced (check console output on startup)
- Verify the bot has the `applications.commands` scope when invited
- Check that the bot has necessary permissions in the server

### Giveaways don't close automatically
- Check the console for error messages
- Verify the bot is running continuously
- Ensure the bot has permission to send messages in the giveaway channel

### Database errors
- The database is created automatically on first run
- If issues occur, you can delete `giveaways.db` to start fresh (⚠️ this deletes all data)

## License

This project is provided as-is for personal use.

## Support

For issues or questions, check the code comments or review the command help with `/gw help`.

