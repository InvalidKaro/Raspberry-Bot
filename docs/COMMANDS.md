# Commands

## Core

- `/help` — interactive help menu
- `/ping` — gateway latency
- `/status` — immediate Raspberry Pi status
- `/userinfo [member]`
- `/serverinfo`
- `/avatar [user]`
- `/roleinfo <role>`
- `/channelinfo [channel]`
- `/profile [member]` — Pillow profile card

## Tickets

- `/ticket panel [channel]`
- `/ticket info`
- `/ticket queue`
- `/ticket claim`
- `/ticket unclaim`
- `/ticket priority`
- `/ticket add <member>`
- `/ticket remove <member>`
- `/ticket notes`
- `/ticket rename <name>`
- `/ticket transfer <staff-member>`
- `/ticket reopen`
- `/ticket delete`

The persistent ticket message also contains Claim, Unclaim, Priority, Internal Note and Close controls.

## Moderation

- `/mod warn <member> <reason>`
- `/mod warnings <member>`
- `/mod case <case_id>`
- `/mod unwarn <case_id>`
- `/mod timeout <member> <minutes> [reason]`
- `/mod untimeout <member> [reason]`
- `/mod kick <member> [reason]`
- `/mod ban <member> [reason] [delete_message_hours]`
- `/mod unban <user_id> [reason]`
- `/mod clear <amount>`
- `/mod lock`
- `/mod unlock`
- `/mod slowmode <seconds>`

## Community

- `/suggest` — opens a suggestion modal
- `/poll` — opens a poll modal with up to four options
- Welcome messages are configured through `/setup welcome`.

## Configuration

- `/setup tickets <category> <log_channel>`
- `/setup staff-add <role> [permission_level]`
- `/setup staff-remove <role>`
- `/setup welcome [channel]`
- `/setup suggestions [channel]`
- `/setup logs [channel]`
- `/setup show`

## Raspberry Pi monitoring

- `/system now`
- `/system setup <status_channel> [alert_channel] [interval_minutes]`
- `/system thresholds [temperature_warning] [temperature_critical] [ram_warning] [disk_warning]`
- `/system disable`
- `/system graph`

The monitor records CPU, temperature, RAM, disk, load, network traffic, bot memory and throttling flags. It also checks `pihole-FTL.service`.

## Developer / owner

Requires the Discord user ID in `OWNER_IDS`.

- `/dev cache-stats`
- `/dev cache-clear [cache_name]`
- `/dev gc`
- `/dev database-optimize`
- `/dev sync`
