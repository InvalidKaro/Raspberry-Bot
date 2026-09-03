from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from database.repositories.settings import SettingsRepository
from helpers.embeds import EmbedFactory


ACCENT = 0x8B5CF6
SUCCESS = 0x31C48D
DANGER = 0xEF4444
MUTED = 0x5865F2
ONBOARDING_STEPS = (
    "Regeln & Grundlagen",
    "Rollen & Zugänge",
    "Einweisung / Schulung",
    "Ansprechpartner & wichtige Links",
    "Abschluss & Rollenfreigabe",
)


def _parse_color(value: str | None) -> discord.Color:
    if not value:
        return discord.Color.blurple()
    raw = value.strip().lower().removeprefix("#").removeprefix("0x")
    try:
        return discord.Color(int(raw, 16))
    except (ValueError, TypeError):
        return discord.Color.blurple()


def _extract_id(value: str) -> int | None:
    match = re.search(r"(\d{15,22})", value or "")
    return int(match.group(1)) if match else None


def _progress_bar(value: int, maximum: int, width: int = 12) -> str:
    if maximum <= 0:
        return "░" * width
    filled = max(0, min(width, round((value / maximum) * width)))
    return "█" * filled + "░" * (width - filled)


def _short(text: Any, limit: int) -> str:
    raw = str(text or "")
    return raw if len(raw) <= limit else raw[: max(0, limit - 1)] + "…"


class OwnerLockedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float | None = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Dieser Wizard gehört einer anderen Person.", ephemeral=True)
        return False


# ---------------------------------------------------------------------------
# Form wizard
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FormDraft:
    name: str = ""
    title: str = ""
    questions: list[str] = field(default_factory=list)


class FormBaseModal(discord.ui.Modal, title="Formular · Basisdaten"):
    name = discord.ui.TextInput(label="Interner Name", placeholder="bewerbung", max_length=60)
    title_input = discord.ui.TextInput(label="Titel", placeholder="Bewerbungsformular", max_length=100)

    def __init__(self, view: "FormWizardView") -> None:
        super().__init__()
        self.wizard = view
        self.name.default = view.draft.name
        self.title_input.default = view.draft.title

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.wizard.draft.name = str(self.name.value).strip().lower()
        self.wizard.draft.title = str(self.title_input.value).strip()
        await interaction.response.edit_message(embed=self.wizard.build_embed(), view=self.wizard)


class FormQuestionsModal(discord.ui.Modal, title="Formular · Fragen"):
    def __init__(self, view: "FormWizardView") -> None:
        super().__init__()
        self.wizard = view
        self.inputs: list[discord.ui.TextInput] = []
        for index in range(5):
            item = discord.ui.TextInput(
                label=f"Frage {index + 1}",
                required=index == 0,
                max_length=250,
                style=discord.TextStyle.paragraph if index >= 2 else discord.TextStyle.short,
                default=view.draft.questions[index] if index < len(view.draft.questions) else None,
            )
            self.inputs.append(item)
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.wizard.draft.questions = [str(item.value).strip() for item in self.inputs if str(item.value).strip()]
        await interaction.response.edit_message(embed=self.wizard.build_embed(), view=self.wizard)


class FormWizardView(OwnerLockedView):
    def __init__(self, bot: commands.Bot, owner_id: int, guild_id: int) -> None:
        super().__init__(owner_id)
        self.bot = bot
        self.guild_id = guild_id
        self.draft = FormDraft()

    def build_embed(self) -> discord.Embed:
        ready = bool(self.draft.name and self.draft.title and self.draft.questions)
        embed = discord.Embed(
            title="🧩 Form Wizard",
            description="Baue ein Formular ohne Slash-Parameter. Bearbeite Basisdaten und Fragen und speichere es anschließend.",
            color=SUCCESS if ready else ACCENT,
        )
        embed.add_field(name="1 · Interner Name", value=f"`{self.draft.name}`" if self.draft.name else "Noch offen", inline=True)
        embed.add_field(name="2 · Titel", value=self.draft.title or "Noch offen", inline=True)
        qtext = "\n".join(f"**{i + 1}.** {q}" for i, q in enumerate(self.draft.questions)) or "Noch keine Fragen."
        embed.add_field(name=f"3 · Fragen ({len(self.draft.questions)}/5)", value=_short(qtext, 1024), inline=False)
        embed.set_footer(text="Speichern schreibt direkt in das bestehende Forms-System.")
        return embed

    @discord.ui.button(label="1 · Basisdaten", emoji="📝", style=discord.ButtonStyle.primary)
    async def base(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(FormBaseModal(self))

    @discord.ui.button(label="2 · Fragen", emoji="❓", style=discord.ButtonStyle.primary)
    async def questions(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(FormQuestionsModal(self))

    @discord.ui.button(label="Speichern", emoji="💾", style=discord.ButtonStyle.success)
    async def save(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.draft.name or not self.draft.title or not self.draft.questions:
            await interaction.response.send_message("Basisdaten und mindestens eine Frage fehlen noch.", ephemeral=True)
            return
        await self.bot.database.execute(
            """
            INSERT INTO forms(guild_id,name,title,questions_json,created_by)
            VALUES(?,?,?,?,?)
            ON CONFLICT(guild_id,name) DO UPDATE SET
                title=excluded.title,
                questions_json=excluded.questions_json,
                created_by=excluded.created_by,
                updated_at=CURRENT_TIMESTAMP
            """,
            (self.guild_id, self.draft.name, self.draft.title, json.dumps(self.draft.questions, ensure_ascii=False), interaction.user.id),
        )
        await interaction.response.send_message(
            f"✅ Formular **{self.draft.title}** gespeichert. Öffnen mit `/creator form_open name:{self.draft.name}`.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Embed wizard
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EmbedDraft:
    title: str = ""
    body: str = ""
    color: str = "5865F2"
    footer: str = ""
    thumbnail: str = ""
    image: str = ""
    channel_id: int | None = None


class EmbedBaseModal(discord.ui.Modal, title="Embed · Inhalt"):
    title_input = discord.ui.TextInput(label="Titel", max_length=256)
    body = discord.ui.TextInput(label="Text", style=discord.TextStyle.paragraph, max_length=4000)
    color = discord.ui.TextInput(label="Farbe (HEX)", required=False, placeholder="8B5CF6", max_length=10)

    def __init__(self, view: "EmbedWizardView") -> None:
        super().__init__()
        self.wizard = view
        self.title_input.default = view.draft.title
        self.body.default = view.draft.body
        self.color.default = view.draft.color

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.wizard.draft.title = str(self.title_input.value).strip()
        self.wizard.draft.body = str(self.body.value).strip()
        self.wizard.draft.color = str(self.color.value).strip() or "5865F2"
        await interaction.response.edit_message(embed=self.wizard.preview_embed(), view=self.wizard)


class EmbedMediaModal(discord.ui.Modal, title="Embed · Medien"):
    footer = discord.ui.TextInput(label="Footer", required=False, max_length=500)
    thumbnail = discord.ui.TextInput(label="Thumbnail URL", required=False, max_length=1000)
    image = discord.ui.TextInput(label="Bild URL", required=False, max_length=1000)

    def __init__(self, view: "EmbedWizardView") -> None:
        super().__init__()
        self.wizard = view
        self.footer.default = view.draft.footer
        self.thumbnail.default = view.draft.thumbnail
        self.image.default = view.draft.image

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.wizard.draft.footer = str(self.footer.value).strip()
        self.wizard.draft.thumbnail = str(self.thumbnail.value).strip()
        self.wizard.draft.image = str(self.image.value).strip()
        await interaction.response.edit_message(embed=self.wizard.preview_embed(), view=self.wizard)


class EmbedChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: "EmbedWizardView") -> None:
        super().__init__(
            placeholder="Zielkanal auswählen · optional",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
            row=2,
        )
        self.wizard = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.wizard.draft.channel_id = int(self.values[0].id)
        await interaction.response.edit_message(embed=self.wizard.preview_embed(), view=self.wizard)


class EmbedWizardView(OwnerLockedView):
    def __init__(self, bot: commands.Bot, owner_id: int, guild_id: int) -> None:
        super().__init__(owner_id)
        self.bot = bot
        self.guild_id = guild_id
        self.draft = EmbedDraft()
        self.add_item(EmbedChannelSelect(self))

    def preview_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.draft.title or "Live Preview · Titel",
            description=self.draft.body or "Hier erscheint dein Embed-Text sofort nach dem Bearbeiten.",
            color=_parse_color(self.draft.color),
        )
        if self.draft.footer:
            embed.set_footer(text=self.draft.footer[:2048])
        if self.draft.thumbnail.startswith(("https://", "http://")):
            embed.set_thumbnail(url=self.draft.thumbnail)
        if self.draft.image.startswith(("https://", "http://")):
            embed.set_image(url=self.draft.image)
        target = f"<#{self.draft.channel_id}>" if self.draft.channel_id else "aktueller Kanal"
        embed.add_field(name="Wizard", value=f"Ziel: {target} · Farbe: `#{self.draft.color.lstrip('#')}`", inline=False)
        return embed

    def final_embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.draft.title[:256], description=self.draft.body[:4096], color=_parse_color(self.draft.color))
        if self.draft.footer:
            embed.set_footer(text=self.draft.footer[:2048])
        if self.draft.thumbnail.startswith(("https://", "http://")):
            embed.set_thumbnail(url=self.draft.thumbnail)
        if self.draft.image.startswith(("https://", "http://")):
            embed.set_image(url=self.draft.image)
        return embed

    @discord.ui.button(label="Inhalt", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def content(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(EmbedBaseModal(self))

    @discord.ui.button(label="Medien", emoji="🖼️", style=discord.ButtonStyle.secondary, row=0)
    async def media(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(EmbedMediaModal(self))

    @discord.ui.button(label="Senden", emoji="🚀", style=discord.ButtonStyle.success, row=0)
    async def send_embed(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.draft.title or not self.draft.body:
            await interaction.response.send_message("Titel und Text fehlen noch.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(self.draft.channel_id) if interaction.guild and self.draft.channel_id else interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.response.send_message("Der Zielkanal ist nicht mehr verfügbar.", ephemeral=True)
            return
        await channel.send(embed=self.final_embed())
        await interaction.response.send_message(f"✅ Embed in {getattr(channel, 'mention', '#Kanal')} gesendet.", ephemeral=True)


# ---------------------------------------------------------------------------
# Panel wizard
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PanelDraft:
    title: str = ""
    description: str = ""
    channel_id: int | None = None
    actions: list[tuple[str, str, str]] = field(default_factory=list)


class PanelBaseModal(discord.ui.Modal, title="Panel · Basis"):
    title_input = discord.ui.TextInput(label="Panel-Titel", max_length=120)
    description = discord.ui.TextInput(label="Beschreibung", required=False, style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, view: "PanelWizardView") -> None:
        super().__init__()
        self.wizard = view
        self.title_input.default = view.draft.title
        self.description.default = view.draft.description

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.wizard.draft.title = str(self.title_input.value).strip()
        self.wizard.draft.description = str(self.description.value).strip()
        await interaction.response.edit_message(embed=self.wizard.build_embed(), view=self.wizard)


class PanelLinkModal(discord.ui.Modal, title="Panel · Link hinzufügen"):
    label = discord.ui.TextInput(label="Button-Text", max_length=80)
    url = discord.ui.TextInput(label="HTTPS URL", max_length=1000)

    def __init__(self, view: "PanelWizardView") -> None:
        super().__init__()
        self.wizard = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        url = str(self.url.value).strip()
        if not url.startswith("https://"):
            await interaction.response.send_message("Link-Buttons akzeptieren nur `https://` URLs.", ephemeral=True)
            return
        self.wizard.add_action(str(self.label.value).strip(), "link", url)
        await interaction.response.edit_message(embed=self.wizard.build_embed(), view=self.wizard)


class PanelInfoModal(discord.ui.Modal, title="Panel · Info hinzufügen"):
    label = discord.ui.TextInput(label="Button-Text", max_length=80)
    text = discord.ui.TextInput(label="Info-Text", style=discord.TextStyle.paragraph, max_length=1800)

    def __init__(self, view: "PanelWizardView") -> None:
        super().__init__()
        self.wizard = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.wizard.add_action(str(self.label.value).strip(), "info", str(self.text.value).strip())
        await interaction.response.edit_message(embed=self.wizard.build_embed(), view=self.wizard)


class PanelRoleSelect(discord.ui.RoleSelect):
    def __init__(self, view: "PanelWizardView") -> None:
        super().__init__(placeholder="Rollen-Button hinzufügen", min_values=1, max_values=1, row=2)
        self.wizard = view

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        if interaction.guild is None or role.managed or role.is_default():
            await interaction.response.send_message("Diese Rolle kann nicht für ein Self-Role-Panel verwendet werden.", ephemeral=True)
            return
        me = interaction.guild.me
        if me is None or role >= me.top_role:
            await interaction.response.send_message("Die Rolle muss unter der höchsten Bot-Rolle liegen.", ephemeral=True)
            return
        self.wizard.add_action(role.name, "role", str(role.id))
        await interaction.response.edit_message(embed=self.wizard.build_embed(), view=self.wizard)


class PanelChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: "PanelWizardView") -> None:
        super().__init__(placeholder="Zielkanal auswählen", channel_types=[discord.ChannelType.text, discord.ChannelType.news], row=3)
        self.wizard = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.wizard.draft.channel_id = int(self.values[0].id)
        await interaction.response.edit_message(embed=self.wizard.build_embed(), view=self.wizard)


class PanelWizardView(OwnerLockedView):
    def __init__(self, bot: commands.Bot, owner_id: int, guild_id: int) -> None:
        super().__init__(owner_id)
        self.bot = bot
        self.guild_id = guild_id
        self.draft = PanelDraft()
        self.add_item(PanelRoleSelect(self))
        self.add_item(PanelChannelSelect(self))

    def add_action(self, label: str, action_type: str, value: str) -> None:
        if len(self.draft.actions) >= 20:
            return
        clean = label.strip()[:80] or action_type.title()
        self.draft.actions.append((clean, action_type, value))

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🧰 Panel Wizard",
            description=self.draft.description or "Erstelle Rollen-, Link- und Info-Panels vollständig über Buttons, Modals und Selects.",
            color=ACCENT,
        )
        embed.add_field(name="Titel", value=self.draft.title or "Noch offen", inline=True)
        embed.add_field(name="Ziel", value=f"<#{self.draft.channel_id}>" if self.draft.channel_id else "aktueller Kanal", inline=True)
        lines = []
        icons = {"role": "🎭", "link": "🔗", "info": "ℹ️"}
        for index, (label, kind, value) in enumerate(self.draft.actions, 1):
            shown = f"<@&{value}>" if kind == "role" else _short(value, 90)
            lines.append(f"**{index}.** {icons.get(kind, '•')} {label} · {shown}")
        embed.add_field(name=f"Aktionen ({len(self.draft.actions)}/20)", value="\n".join(lines) or "Noch keine Aktionen.", inline=False)
        embed.set_footer(text="Rollen über den Select hinzufügen; Links/Infos über die Buttons.")
        return embed

    @discord.ui.button(label="Basis", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def base(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(PanelBaseModal(self))

    @discord.ui.button(label="Link +", emoji="🔗", style=discord.ButtonStyle.secondary, row=0)
    async def add_link(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if len(self.draft.actions) >= 20:
            await interaction.response.send_message("Das Panel hat bereits 20 Aktionen.", ephemeral=True)
            return
        await interaction.response.send_modal(PanelLinkModal(self))

    @discord.ui.button(label="Info +", emoji="ℹ️", style=discord.ButtonStyle.secondary, row=0)
    async def add_info(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if len(self.draft.actions) >= 20:
            await interaction.response.send_message("Das Panel hat bereits 20 Aktionen.", ephemeral=True)
            return
        await interaction.response.send_modal(PanelInfoModal(self))

    @discord.ui.button(label="Letzte löschen", emoji="↩️", style=discord.ButtonStyle.secondary, row=0)
    async def undo(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.draft.actions:
            self.draft.actions.pop()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Veröffentlichen", emoji="🚀", style=discord.ButtonStyle.success, row=1)
    async def publish(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.draft.title or not self.draft.actions:
            await interaction.response.send_message("Panel-Titel und mindestens eine Aktion fehlen.", ephemeral=True)
            return
        target = interaction.guild.get_channel(self.draft.channel_id) if interaction.guild and self.draft.channel_id else interaction.channel
        if not isinstance(target, discord.abc.Messageable):
            await interaction.response.send_message("Der Zielkanal ist nicht verfügbar.", ephemeral=True)
            return
        panel_id = await self.bot.database.execute(
            "INSERT INTO panel_messages(guild_id,title,created_by) VALUES(?,?,?)",
            (self.guild_id, self.draft.title, interaction.user.id),
        )
        for pos, (label, action_type, value) in enumerate(self.draft.actions):
            await self.bot.database.execute(
                "INSERT INTO panel_actions(panel_id,guild_id,label,action_type,value,position) VALUES(?,?,?,?,?,?)",
                (panel_id, self.guild_id, label, action_type, value, pos),
            )
        actions = await self.bot.database.fetchall("SELECT * FROM panel_actions WHERE panel_id=? ORDER BY position,id", (panel_id,))
        from cogs.community.creator_suite import StoredPanelView

        view = StoredPanelView(actions)
        message = await target.send(
            embed=EmbedFactory.info(title=self.draft.title, description=self.draft.description or "Wähle eine Aktion:"),
            view=view,
        )
        await self.bot.database.execute(
            "UPDATE panel_messages SET channel_id=?,message_id=? WHERE id=?",
            (message.channel.id, message.id, panel_id),
        )
        self.bot.add_view(view, message_id=message.id)
        await interaction.response.send_message("✅ Panel veröffentlicht.", ephemeral=True)


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------


SETUP_STEPS = (
    ("Logs", "Audit- und Utility-Logs zentral festlegen."),
    ("Tickets", "Ticket-Kategorie, Ticket-Log und Staff-Rolle."),
    ("Welcome & Rollen", "Welcome-Kanal und Auto-Rolle."),
    ("Moderation", "Bot-Moderatorrolle festlegen."),
    ("Workspace", "Standardkanal für Workspace-/Organisationsthemen merken."),
    ("Monitoring", "Status- und Alert-Kanal sowie Monitoring aktivieren."),
    ("Review", "Alle Werte prüfen und gesammelt anwenden."),
)


class SetupChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, wizard: "SetupWizardView", field_name: str, placeholder: str, channel_types: list[discord.ChannelType], row: int) -> None:
        super().__init__(placeholder=placeholder, channel_types=channel_types, min_values=1, max_values=1, row=row)
        self.wizard = wizard
        self.field_name = field_name

    async def callback(self, interaction: discord.Interaction) -> None:
        self.wizard.data[self.field_name] = int(self.values[0].id)
        await interaction.response.edit_message(embed=self.wizard.build_embed(), view=self.wizard)


class SetupRoleSelect(discord.ui.RoleSelect):
    def __init__(self, wizard: "SetupWizardView", field_name: str, placeholder: str, row: int) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, row=row)
        self.wizard = wizard
        self.field_name = field_name

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        self.wizard.data[self.field_name] = int(role.id)
        await interaction.response.edit_message(embed=self.wizard.build_embed(), view=self.wizard)


class SetupNavButton(discord.ui.Button):
    def __init__(self, wizard: "SetupWizardView", action: str, label: str, style: discord.ButtonStyle, row: int = 4, emoji: str | None = None) -> None:
        super().__init__(label=label, style=style, row=row, emoji=emoji)
        self.wizard = wizard
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.wizard.handle_action(interaction, self.action)


class SetupWizardView(OwnerLockedView):
    def __init__(self, bot: commands.Bot, owner_id: int, guild_id: int, data: dict[str, Any]) -> None:
        super().__init__(owner_id, timeout=1200)
        self.bot = bot
        self.guild_id = guild_id
        self.data = data
        self.step = 0
        self.rebuild()

    def _channel(self, key: str) -> str:
        value = self.data.get(key)
        return f"<#{value}>" if value else "—"

    def _role(self, key: str) -> str:
        value = self.data.get(key)
        return f"<@&{value}>" if value else "—"

    def build_embed(self) -> discord.Embed:
        name, description = SETUP_STEPS[self.step]
        embed = discord.Embed(
            title=f"⚙️ Setup Wizard · {self.step + 1}/{len(SETUP_STEPS)} · {name}",
            description=description,
            color=ACCENT,
        )
        embed.add_field(name="Logs", value=self._channel("general_log_channel_id"), inline=True)
        embed.add_field(name="Tickets", value=f"Kategorie {self._channel('ticket_category_id')}\nLog {self._channel('ticket_log_channel_id')}\nStaff {self._role('ticket_staff_role_id')}", inline=True)
        embed.add_field(name="Welcome", value=f"Kanal {self._channel('welcome_channel_id')}\nAuto-Rolle {self._role('auto_role_id')}", inline=True)
        embed.add_field(name="Moderation", value=self._role("moderator_role_id"), inline=True)
        embed.add_field(name="Workspace", value=self._channel("workspace_channel_id"), inline=True)
        monitor = "✅ aktiv" if self.data.get("monitoring_enabled") else "⏸️ aus"
        embed.add_field(name="Monitoring", value=f"{monitor}\nStatus {self._channel('status_channel_id')}\nAlerts {self._channel('alert_channel_id')}", inline=True)
        embed.set_footer(text="Bestehende Werte werden geladen; nur deine Änderungen werden beim Anwenden übernommen.")
        return embed

    def rebuild(self) -> None:
        self.clear_items()
        text_types = [discord.ChannelType.text, discord.ChannelType.news]
        if self.step == 0:
            self.add_item(SetupChannelSelect(self, "general_log_channel_id", "Audit-/Log-Kanal", text_types, 0))
        elif self.step == 1:
            self.add_item(SetupChannelSelect(self, "ticket_category_id", "Ticket-Kategorie", [discord.ChannelType.category], 0))
            self.add_item(SetupChannelSelect(self, "ticket_log_channel_id", "Ticket-Log-Kanal", text_types, 1))
            self.add_item(SetupRoleSelect(self, "ticket_staff_role_id", "Ticket-Staff-Rolle", 2))
        elif self.step == 2:
            self.add_item(SetupChannelSelect(self, "welcome_channel_id", "Welcome-Kanal", text_types, 0))
            self.add_item(SetupRoleSelect(self, "auto_role_id", "Auto-Rolle", 1))
        elif self.step == 3:
            self.add_item(SetupRoleSelect(self, "moderator_role_id", "Bot-Moderatorrolle", 0))
        elif self.step == 4:
            self.add_item(SetupChannelSelect(self, "workspace_channel_id", "Workspace-Standardkanal", text_types, 0))
        elif self.step == 5:
            self.add_item(SetupChannelSelect(self, "status_channel_id", "Monitoring-Statuskanal", text_types, 0))
            self.add_item(SetupChannelSelect(self, "alert_channel_id", "Monitoring-Alertkanal", text_types, 1))
            self.add_item(SetupNavButton(self, "toggle-monitor", "Monitoring an/aus", discord.ButtonStyle.secondary, row=2, emoji="📈"))
        if self.step > 0:
            self.add_item(SetupNavButton(self, "prev", "Zurück", discord.ButtonStyle.secondary, emoji="⬅️"))
        if self.step < len(SETUP_STEPS) - 1:
            self.add_item(SetupNavButton(self, "next", "Weiter", discord.ButtonStyle.primary, emoji="➡️"))
        else:
            self.add_item(SetupNavButton(self, "apply", "Alles anwenden", discord.ButtonStyle.success, emoji="✅"))

    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        if action == "prev":
            self.step = max(0, self.step - 1)
        elif action == "next":
            self.step = min(len(SETUP_STEPS) - 1, self.step + 1)
        elif action == "toggle-monitor":
            self.data["monitoring_enabled"] = not bool(self.data.get("monitoring_enabled"))
        elif action == "apply":
            await self.apply(interaction)
            return
        self.rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def apply(self, interaction: discord.Interaction) -> None:
        repo = SettingsRepository(self.bot.database, self.bot.cache)
        await repo.update_guild_settings(
            self.guild_id,
            ticket_category_id=self.data.get("ticket_category_id"),
            ticket_log_channel_id=self.data.get("ticket_log_channel_id"),
            welcome_channel_id=self.data.get("welcome_channel_id"),
            auto_role_id=self.data.get("auto_role_id"),
            general_log_channel_id=self.data.get("general_log_channel_id"),
        )
        staff = self.data.get("ticket_staff_role_id")
        if staff:
            await self.bot.database.execute(
                "INSERT OR IGNORE INTO ticket_staff_roles(guild_id,role_id,permission_level) VALUES(?,?,10)",
                (self.guild_id, int(staff)),
            )
        moderator = self.data.get("moderator_role_id")
        if moderator:
            await self.bot.database.execute(
                """
                INSERT INTO bot_access_roles(guild_id,role_id,level,created_by) VALUES(?,?, 'moderator', ?)
                ON CONFLICT(guild_id,role_id) DO UPDATE SET level='moderator',created_by=excluded.created_by
                """,
                (self.guild_id, int(moderator), interaction.user.id),
            )
        await self.bot.database.execute(
            """
            INSERT INTO wizard_server_config(guild_id,workspace_channel_id,updated_by)
            VALUES(?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET workspace_channel_id=excluded.workspace_channel_id,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP
            """,
            (self.guild_id, self.data.get("workspace_channel_id"), interaction.user.id),
        )
        await self.bot.database.execute(
            """
            INSERT INTO system_monitor_config(guild_id,enabled,status_channel_id,alert_channel_id,interval_seconds)
            VALUES(?,?,?,?,60)
            ON CONFLICT(guild_id) DO UPDATE SET
                enabled=excluded.enabled,
                status_channel_id=excluded.status_channel_id,
                alert_channel_id=excluded.alert_channel_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                self.guild_id,
                1 if self.data.get("monitoring_enabled") else 0,
                self.data.get("status_channel_id"),
                self.data.get("alert_channel_id"),
            ),
        )
        self.clear_items()
        await interaction.response.edit_message(
            embed=EmbedFactory.success(title="Setup Wizard abgeschlossen", description="Server-Konfiguration, Rollen, Workspace-Referenz und Monitoring wurden gespeichert."),
            view=self,
        )


# ---------------------------------------------------------------------------
# Onboarding wizard
# ---------------------------------------------------------------------------


class OnboardingNoteModal(discord.ui.Modal, title="Onboarding · Notiz"):
    note = discord.ui.TextInput(label="Notiz", style=discord.TextStyle.paragraph, max_length=1200)

    def __init__(self, view: "OnboardingRunView") -> None:
        super().__init__()
        self.wizard = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        row = await self.wizard.bot.database.fetchone("SELECT notes FROM wizard_onboarding_runs WHERE id=?", (self.wizard.run_id,))
        existing = str(row["notes"] or "") if row else ""
        new_note = str(self.note.value).strip()
        combined = (existing + ("\n" if existing else "") + f"[{interaction.user.display_name}] {new_note}")[-3500:]
        await self.wizard.bot.database.execute(
            "UPDATE wizard_onboarding_runs SET notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (combined, self.wizard.run_id),
        )
        await interaction.response.edit_message(embed=await self.wizard.build_embed(), view=self.wizard)


class OnboardingRoleSelect(discord.ui.RoleSelect):
    def __init__(self, view: "OnboardingRunView") -> None:
        super().__init__(placeholder="Abschlussrolle auswählen · optional", min_values=1, max_values=1, row=0)
        self.wizard = view

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        await self.wizard.bot.database.execute(
            "UPDATE wizard_onboarding_runs SET final_role_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (role.id, self.wizard.run_id),
        )
        await interaction.response.edit_message(embed=await self.wizard.build_embed(), view=self.wizard)


class OnboardingRunView(discord.ui.View):
    def __init__(self, bot: commands.Bot, run_id: int, manager_id: int) -> None:
        super().__init__(timeout=3600)
        self.bot = bot
        self.run_id = int(run_id)
        self.manager_id = int(manager_id)
        self.add_item(OnboardingRoleSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.manager_id:
            return True
        if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_roles:
            return True
        await interaction.response.send_message("Nur zuständige Mitarbeiter können diesen Onboarding-Lauf steuern.", ephemeral=True)
        return False

    async def _row(self):
        return await self.bot.database.fetchone("SELECT * FROM wizard_onboarding_runs WHERE id=?", (self.run_id,))

    async def build_embed(self) -> discord.Embed:
        row = await self._row()
        if not row:
            return EmbedFactory.error(title="Onboarding nicht gefunden", description="Dieser Lauf existiert nicht mehr.")
        step = int(row["step"])
        status = str(row["status"])
        lines = []
        for index, name in enumerate(ONBOARDING_STEPS):
            marker = "✅" if index < step else "➡️" if index == step and status == "active" else "▫️"
            lines.append(f"{marker} **{index + 1}. {name}**")
        embed = discord.Embed(
            title=f"🧭 Onboarding · <@{row['user_id']}>",
            description="\n".join(lines),
            color=SUCCESS if status == "completed" else ACCENT,
        )
        embed.add_field(name="Status", value=status.title(), inline=True)
        embed.add_field(name="Abschlussrolle", value=f"<@&{row['final_role_id']}>" if row["final_role_id"] else "—", inline=True)
        notes = str(row["notes"] or "").strip()
        if notes:
            embed.add_field(name="Notizen", value=_short(notes, 1024), inline=False)
        embed.set_footer(text=f"Run #{self.run_id} · erneut /onboardingwizard starten, um einen aktiven Lauf später fortzusetzen")
        return embed

    @discord.ui.button(label="Zurück", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        row = await self._row()
        if not row or str(row["status"]) != "active":
            await interaction.response.send_message("Dieser Lauf ist bereits beendet.", ephemeral=True)
            return
        step = max(0, int(row["step"]) - 1)
        await self.bot.database.execute("UPDATE wizard_onboarding_runs SET step=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (step, self.run_id))
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Schritt erledigt", emoji="✅", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        row = await self._row()
        if not row or str(row["status"]) != "active":
            await interaction.response.send_message("Dieser Lauf ist bereits beendet.", ephemeral=True)
            return
        step = min(len(ONBOARDING_STEPS) - 1, int(row["step"]) + 1)
        await self.bot.database.execute("UPDATE wizard_onboarding_runs SET step=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (step, self.run_id))
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Notiz", emoji="📝", style=discord.ButtonStyle.secondary, row=1)
    async def note(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(OnboardingNoteModal(self))

    @discord.ui.button(label="Abschließen", emoji="🎓", style=discord.ButtonStyle.success, row=1)
    async def finish(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        row = await self._row()
        if not row or str(row["status"]) != "active":
            await interaction.response.send_message("Dieser Lauf ist bereits beendet.", ephemeral=True)
            return
        if int(row["step"]) < len(ONBOARDING_STEPS) - 1:
            await interaction.response.send_message("Gehe zuerst bis zum letzten Onboarding-Schritt.", ephemeral=True)
            return
        if interaction.guild and row["final_role_id"]:
            member = interaction.guild.get_member(int(row["user_id"]))
            role = interaction.guild.get_role(int(row["final_role_id"]))
            me = interaction.guild.me
            if member and role and not role.managed and me and role < me.top_role:
                await member.add_roles(role, reason=f"Onboarding wizard #{self.run_id} completed")
        await self.bot.database.execute(
            "UPDATE wizard_onboarding_runs SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (self.run_id,),
        )
        self.clear_items()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Abbrechen", emoji="✖️", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.database.execute("UPDATE wizard_onboarding_runs SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=?", (self.run_id,))
        self.clear_items()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)


# ---------------------------------------------------------------------------
# Offboarding wizard
# ---------------------------------------------------------------------------


class OffboardingView(discord.ui.View):
    def __init__(self, bot: commands.Bot, run_id: int, manager_id: int) -> None:
        super().__init__(timeout=1800)
        self.bot = bot
        self.run_id = int(run_id)
        self.manager_id = int(manager_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.manager_id:
            return True
        if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message("Nur Server-Manager können dieses Offboarding steuern.", ephemeral=True)
        return False

    async def _row(self):
        return await self.bot.database.fetchone("SELECT * FROM wizard_offboarding_runs WHERE id=?", (self.run_id,))

    async def _counts(self, guild_id: int, user_id: int) -> dict[str, int]:
        tasks = await self.bot.database.fetchone(
            "SELECT COUNT(*) c FROM workspace_tasks WHERE guild_id=? AND assigned_to=? AND status NOT IN ('done','closed')",
            (guild_id, user_id),
        )
        claimed = await self.bot.database.fetchone(
            "SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND claimed_by=? AND status!='closed'",
            (guild_id, user_id),
        )
        memberships = await self.bot.database.fetchone(
            "SELECT COUNT(*) c FROM ticket_members tm JOIN tickets t ON t.id=tm.ticket_id WHERE t.guild_id=? AND tm.user_id=?",
            (guild_id, user_id),
        )
        return {
            "tasks": int(tasks["c"] if tasks else 0),
            "claimed": int(claimed["c"] if claimed else 0),
            "memberships": int(memberships["c"] if memberships else 0),
        }

    async def build_embed(self, guild: discord.Guild | None) -> discord.Embed:
        row = await self._row()
        if not row:
            return EmbedFactory.error(title="Offboarding nicht gefunden", description="Dieser Lauf existiert nicht mehr.")
        guild_id = int(row["guild_id"])
        user_id = int(row["user_id"])
        counts = await self._counts(guild_id, user_id)
        member = guild.get_member(user_id) if guild else None
        role_count = len([r for r in member.roles if not r.is_default() and not r.managed]) if member else 0
        embed = discord.Embed(
            title=f"📤 Offboarding · <@{user_id}>",
            description="Strukturierte Übergabe ohne Datenlöschung. Perso-Daten werden von diesem Wizard ausdrücklich **nicht** verändert.",
            color=SUCCESS if str(row["status"]) == "completed" else DANGER,
        )
        embed.add_field(name="Rollen", value=f"{'✅' if row['roles_done'] else '⬜'} {role_count} aktuell entfernbar", inline=True)
        embed.add_field(name="Offene Tasks", value=f"{'✅' if row['tasks_done'] else '⬜'} {counts['tasks']} zugewiesen", inline=True)
        embed.add_field(name="Tickets", value=f"{'✅' if row['tickets_done'] else '⬜'} {counts['claimed']} Claims · {counts['memberships']} Mitgliedschaften", inline=True)
        embed.add_field(name="Was passiert?", value="Rollen werden entfernt; offene Workspace-Tasks werden nur **entkoppelt**; Ticket-Claims und Ticket-Mitgliedschaften werden gelöst. Nichts wird gelöscht.", inline=False)
        embed.set_footer(text=f"Run #{self.run_id}")
        return embed

    @discord.ui.button(label="Rollen entfernen", emoji="🎭", style=discord.ButtonStyle.danger)
    async def roles(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        row = await self._row()
        if not row or not interaction.guild:
            return
        member = interaction.guild.get_member(int(row["user_id"]))
        me = interaction.guild.me
        if member and me:
            removable = [r for r in member.roles if not r.is_default() and not r.managed and r < me.top_role]
            if removable:
                await member.remove_roles(*removable, reason=f"Offboarding wizard #{self.run_id}")
        await self.bot.database.execute("UPDATE wizard_offboarding_runs SET roles_done=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (self.run_id,))
        await interaction.response.edit_message(embed=await self.build_embed(interaction.guild), view=self)

    @discord.ui.button(label="Tasks lösen", emoji="📋", style=discord.ButtonStyle.primary)
    async def tasks(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        row = await self._row()
        if not row:
            return
        await self.bot.database.execute(
            "UPDATE workspace_tasks SET assigned_to=NULL,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND assigned_to=? AND status NOT IN ('done','closed')",
            (row["guild_id"], row["user_id"]),
        )
        await self.bot.database.execute("UPDATE wizard_offboarding_runs SET tasks_done=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (self.run_id,))
        await interaction.response.edit_message(embed=await self.build_embed(interaction.guild), view=self)

    @discord.ui.button(label="Tickets lösen", emoji="🎫", style=discord.ButtonStyle.primary)
    async def tickets(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        row = await self._row()
        if not row:
            return
        await self.bot.database.execute(
            "UPDATE tickets SET claimed_by=NULL,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND claimed_by=? AND status!='closed'",
            (row["guild_id"], row["user_id"]),
        )
        await self.bot.database.execute(
            "DELETE FROM ticket_members WHERE user_id=? AND ticket_id IN (SELECT id FROM tickets WHERE guild_id=?)",
            (row["user_id"], row["guild_id"]),
        )
        await self.bot.database.execute("UPDATE wizard_offboarding_runs SET tickets_done=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (self.run_id,))
        await interaction.response.edit_message(embed=await self.build_embed(interaction.guild), view=self)

    @discord.ui.button(label="Refresh", emoji="↻", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=await self.build_embed(interaction.guild), view=self)

    @discord.ui.button(label="Abschließen", emoji="✅", style=discord.ButtonStyle.success)
    async def finish(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        row = await self._row()
        if not row:
            return
        if not all(int(row[key]) for key in ("roles_done", "tasks_done", "tickets_done")):
            await interaction.response.send_message("Rollen, Tasks und Tickets müssen zuerst abgearbeitet werden.", ephemeral=True)
            return
        await self.bot.database.execute("UPDATE wizard_offboarding_runs SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (self.run_id,))
        self.clear_items()
        await interaction.response.edit_message(embed=await self.build_embed(interaction.guild), view=self)


# ---------------------------------------------------------------------------
# Cog / commands
# ---------------------------------------------------------------------------


class WizardSuite(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS wizard_server_config(
                guild_id INTEGER PRIMARY KEY,
                workspace_channel_id INTEGER,
                updated_by INTEGER,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS wizard_onboarding_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                step INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                final_role_id INTEGER,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS wizard_offboarding_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                roles_done INTEGER NOT NULL DEFAULT 0,
                tasks_done INTEGER NOT NULL DEFAULT 0,
                tickets_done INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    @app_commands.command(name="formwizard", description="Formular interaktiv Schritt für Schritt erstellen.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def formwizard(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        view = FormWizardView(self.bot, interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @app_commands.command(name="embedwizard", description="Embed mit Live-Preview interaktiv erstellen.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def embedwizard(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        view = EmbedWizardView(self.bot, interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(embed=view.preview_embed(), view=view, ephemeral=True)

    @app_commands.command(name="panelwizard", description="Rollen-, Link- und Info-Panel interaktiv erstellen.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def panelwizard(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        view = PanelWizardView(self.bot, interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @app_commands.command(name="setupwizard", description="Server-Konfiguration Schritt für Schritt einrichten.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setupwizard(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        repo = SettingsRepository(self.bot.database, self.bot.cache)
        settings = await repo.get_guild_settings(interaction.guild_id)
        monitor = await self.bot.database.fetchone("SELECT * FROM system_monitor_config WHERE guild_id=?", (interaction.guild_id,))
        workspace = await self.bot.database.fetchone("SELECT * FROM wizard_server_config WHERE guild_id=?", (interaction.guild_id,))
        staff_roles = await repo.list_ticket_staff_roles(interaction.guild_id)
        moderator = await self.bot.database.fetchone(
            "SELECT role_id FROM bot_access_roles WHERE guild_id=? AND level='moderator' ORDER BY created_at DESC LIMIT 1",
            (interaction.guild_id,),
        )
        data: dict[str, Any] = {
            "general_log_channel_id": settings.get("general_log_channel_id"),
            "ticket_category_id": settings.get("ticket_category_id"),
            "ticket_log_channel_id": settings.get("ticket_log_channel_id"),
            "ticket_staff_role_id": int(staff_roles[0]) if staff_roles else None,
            "welcome_channel_id": settings.get("welcome_channel_id"),
            "auto_role_id": settings.get("auto_role_id"),
            "moderator_role_id": int(moderator["role_id"]) if moderator else None,
            "workspace_channel_id": int(workspace["workspace_channel_id"]) if workspace and workspace["workspace_channel_id"] else None,
            "monitoring_enabled": bool(int(monitor["enabled"])) if monitor else False,
            "status_channel_id": int(monitor["status_channel_id"]) if monitor and monitor["status_channel_id"] else None,
            "alert_channel_id": int(monitor["alert_channel_id"]) if monitor and monitor["alert_channel_id"] else None,
        }
        view = SetupWizardView(self.bot, interaction.user.id, interaction.guild_id, data)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @app_commands.command(name="onboardingwizard", description="Mitarbeiter/User durch einen strukturierten Onboarding-Lauf führen.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def onboardingwizard(self, interaction: discord.Interaction, mitglied: discord.Member) -> None:
        if interaction.guild_id is None:
            return
        active = await self.bot.database.fetchone(
            "SELECT id,created_by FROM wizard_onboarding_runs WHERE guild_id=? AND user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (interaction.guild_id, mitglied.id),
        )
        if active:
            run_id = int(active["id"])
            manager_id = int(active["created_by"])
        else:
            run_id = await self.bot.database.execute(
                "INSERT INTO wizard_onboarding_runs(guild_id,user_id,created_by) VALUES(?,?,?)",
                (interaction.guild_id, mitglied.id, interaction.user.id),
            )
            manager_id = interaction.user.id
        view = OnboardingRunView(self.bot, run_id, manager_id)
        await interaction.response.send_message(embed=await view.build_embed(), view=view)

    @app_commands.command(name="offboarding", description="Rollen, Tasks und Ticket-Zuständigkeiten strukturiert lösen.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def offboarding(self, interaction: discord.Interaction, mitglied: discord.Member) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            return
        if mitglied.id == interaction.guild.owner_id:
            await interaction.response.send_message("Der Server-Owner kann nicht über den Offboarding-Wizard bearbeitet werden.", ephemeral=True)
            return
        if self.bot.user and mitglied.id == self.bot.user.id:
            await interaction.response.send_message("Der Bot kann sich nicht selbst offboarden.", ephemeral=True)
            return
        active = await self.bot.database.fetchone(
            "SELECT id,created_by FROM wizard_offboarding_runs WHERE guild_id=? AND user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (interaction.guild_id, mitglied.id),
        )
        if active:
            run_id = int(active["id"])
            manager_id = int(active["created_by"])
        else:
            run_id = await self.bot.database.execute(
                "INSERT INTO wizard_offboarding_runs(guild_id,user_id,created_by) VALUES(?,?,?)",
                (interaction.guild_id, mitglied.id, interaction.user.id),
            )
            manager_id = interaction.user.id
        view = OffboardingView(self.bot, run_id, manager_id)
        await interaction.response.send_message(embed=await view.build_embed(interaction.guild), view=view, ephemeral=True)

    @app_commands.command(name="profilecard", description="Hochwertige Profilkarte mit XP, Achievements, Tasks, Rollen und Statistiken.")
    @app_commands.guild_only()
    async def profilecard(self, interaction: discord.Interaction, mitglied: discord.Member | None = None) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            return
        member = mitglied or interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Profilkarten funktionieren nur für Servermitglieder.", ephemeral=True)
            return
        xp_row = await self.bot.database.fetchone(
            "SELECT xp,level,message_count FROM xp_profiles WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id),
        )
        xp = int(xp_row["xp"]) if xp_row else 0
        level = int(xp_row["level"]) if xp_row else int(math.sqrt(xp / 100))
        messages = int(xp_row["message_count"]) if xp_row else 0
        rank_row = await self.bot.database.fetchone(
            "SELECT COUNT(*)+1 rank FROM xp_profiles WHERE guild_id=? AND xp>?",
            (interaction.guild_id, xp),
        )
        rank = int(rank_row["rank"]) if rank_row else 1
        achievements = await self.bot.database.fetchall(
            """
            SELECT a.title,a.description,ua.unlocked_at
            FROM user_achievements ua JOIN achievements a ON a.id=ua.achievement_id
            WHERE ua.guild_id=? AND ua.user_id=?
            ORDER BY ua.unlocked_at DESC LIMIT 6
            """,
            (interaction.guild_id, member.id),
        )
        ach_count_row = await self.bot.database.fetchone(
            "SELECT COUNT(*) c FROM user_achievements WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id),
        )
        task_row = await self.bot.database.fetchone(
            "SELECT COUNT(*) c FROM workspace_tasks WHERE guild_id=? AND assigned_to=? AND status NOT IN ('done','closed')",
            (interaction.guild_id, member.id),
        )
        opened_tickets = await self.bot.database.fetchone(
            "SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND opener_id=?",
            (interaction.guild_id, member.id),
        )
        claimed_tickets = await self.bot.database.fetchone(
            "SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND claimed_by=?",
            (interaction.guild_id, member.id),
        )
        command_row = await self.bot.database.fetchone(
            "SELECT COUNT(*) c FROM command_usage WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id),
        )
        try:
            game_row = await self.bot.database.fetchone(
                "SELECT COALESCE(SUM(played),0) played,COALESCE(SUM(wins),0) wins FROM game_stats WHERE guild_id=? AND user_id=?",
                (interaction.guild_id, member.id),
            )
        except Exception:
            game_row = None

        current_floor = level * level * 100
        next_floor = (level + 1) * (level + 1) * 100
        into_level = max(0, xp - current_floor)
        level_span = max(1, next_floor - current_floor)
        bar = _progress_bar(into_level, level_span)
        role_names = [role.mention for role in member.roles if not role.is_default() and not role.managed]
        role_text = " ".join(role_names[-10:]) or "—"
        achievement_text = "\n".join(f"🏅 **{r['title']}** · {_short(r['description'], 70)}" for r in achievements) or "Noch keine Achievements."

        embed = discord.Embed(
            title=f"✨ Profilkarte · {member.display_name}",
            description=(
                f"**Level {level}** · **{xp:,} XP** · Server-Rang **#{rank}**\n"
                f"`{bar}` {into_level:,}/{level_span:,} XP bis Level {level + 1}"
            ),
            color=member.color if member.color.value else discord.Color(ACCENT),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Community", value=f"💬 {messages:,} gewertete Nachrichten\n🏆 {int(ach_count_row['c']) if ach_count_row else 0} Achievements\n⌨️ {int(command_row['c']) if command_row else 0} Commands", inline=True)
        embed.add_field(name="Organisation", value=f"📋 {int(task_row['c']) if task_row else 0} offene Tasks\n🎫 {int(opened_tickets['c']) if opened_tickets else 0} Tickets erstellt\n🛠️ {int(claimed_tickets['c']) if claimed_tickets else 0} Tickets bearbeitet", inline=True)
        embed.add_field(name="Arcade", value=f"🎮 {int(game_row['played']) if game_row else 0} Spiele\n🥇 {int(game_row['wins']) if game_row else 0} Siege", inline=True)
        embed.add_field(name="Neueste Achievements", value=_short(achievement_text, 1024), inline=False)
        embed.add_field(name="Rollen", value=_short(role_text, 1024), inline=False)
        joined = discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "—"
        created = discord.utils.format_dt(member.created_at, style="D")
        embed.set_footer(text=f"Serverbeitritt {joined} · Discord-Konto {created} · User-ID {member.id}")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WizardSuite(bot))
