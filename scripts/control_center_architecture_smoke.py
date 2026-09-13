from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discord  # noqa: E402
from discord import app_commands  # noqa: E402
from discord.ext import commands  # noqa: E402

from services.action_registry import action_ids_for_command, action_specs_for_command  # noqa: E402
from services.health_checks import HEALTH_SERVICES, HealthResult, summarize  # noqa: E402
from services.server_score import ScoreInput, calculate_server_score  # noqa: E402
from views.command_actions import (  # noqa: E402
    RelatedCommandsView,
    _button_invokable,
    _execution_mode,
    _parse_value,
    _related_commands,
)


def test_action_contexts() -> None:
    admin = action_ids_for_command("admin healthcheck")
    media = action_ids_for_command("media radio")
    mesh = action_ids_for_command("mesh status")
    generic = action_ids_for_command("userinfo")

    assert admin[:4] == ("system_status", "quick_check", "services", "diagnostics")
    assert "services" in mesh
    assert "related" in media
    assert generic == ("related", "control_center")
    assert len(action_specs_for_command("admin diagnose")) <= 5


def test_smart_command_flows() -> None:
    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)
    group = app_commands.Group(name="demo", description="Demo command group")

    @group.command(name="status", description="Current status")
    async def demo_status(interaction: discord.Interaction) -> None:
        del interaction

    @group.command(name="ping", description="Ping without arguments")
    async def demo_ping(interaction: discord.Interaction) -> None:
        del interaction

    @group.command(name="lookup", description="Lookup requiring text input")
    async def demo_lookup(interaction: discord.Interaction, query: str) -> None:
        del interaction, query

    @group.command(name="count", description="Count with integer input")
    async def demo_count(interaction: discord.Interaction, amount: int) -> None:
        del interaction, amount

    @group.command(name="member", description="Complex Discord object")
    async def demo_member(interaction: discord.Interaction, member: discord.Member) -> None:
        del interaction, member

    bot.tree.add_command(group)

    related = _related_commands(bot, "demo status")
    names = {command.qualified_name for command in related}
    assert {"demo ping", "demo lookup", "demo count", "demo member"}.issubset(names)

    assert _button_invokable(demo_ping) is True
    assert _execution_mode(demo_ping) == "direct"
    assert _execution_mode(demo_lookup) == "modal"
    assert _execution_mode(demo_count) == "modal"
    assert _execution_mode(demo_member) == "slash"

    query_param = next(parameter for parameter in demo_lookup.parameters if parameter.name == "query")
    count_param = next(parameter for parameter in demo_count.parameters if parameter.name == "amount")
    assert _parse_value(query_param, "hello world") == "hello world"
    assert _parse_value(count_param, "42") == 42

    view = RelatedCommandsView(bot, 123, "demo status")
    labels = {str(getattr(item, "label", "")) for item in view.children}
    assert "/demo ping" in labels
    assert "/demo lookup" in labels
    assert "/demo count" in labels
    assert "/demo member" not in labels
    assert any(isinstance(item, discord.ui.Select) for item in view.children)


def test_health_model() -> None:
    assert HEALTH_SERVICES["radar"] == "homepi-flight-radar"
    assert HEALTH_SERVICES["mesh"] == "raspberry-meshtastic"
    results = [
        HealthResult("bot", "online", 3.0, 10.0, "ok"),
        HealthResult("radar", "degraded", 5.0, 11.0, "starting"),
    ]
    summary = summarize(results)
    assert summary["status"] == "degraded"
    assert summary["counts"] == {"online": 1, "degraded": 1, "offline": 0}
    assert summary["last_check"] == 11.0


def test_server_score() -> None:
    healthy = calculate_server_score(
        ScoreInput(
            cpu_percent=18,
            ram_percent=52,
            temperature_c=42,
            disk_percent=35,
            services_online_ratio=1.0,
            network_ok=True,
            bot_errors_24h=0,
            uptime_seconds=86400,
        )
    )
    stressed = calculate_server_score(
        ScoreInput(
            cpu_percent=90,
            ram_percent=93,
            temperature_c=80,
            disk_percent=92,
            services_online_ratio=0.5,
            network_ok=False,
            internet_outages_24h=3,
            service_crashes_24h=6,
            bot_errors_24h=12,
            reboots_24h=2,
            uptime_seconds=600,
        )
    )
    short_cpu_spike = calculate_server_score(
        ScoreInput(
            cpu_percent=75,
            ram_percent=52,
            temperature_c=42,
            disk_percent=35,
            services_online_ratio=1.0,
            network_ok=True,
            bot_errors_24h=0,
            uptime_seconds=86400,
        )
    )

    assert 0 <= stressed.score < healthy.score <= 100
    assert healthy.grade in {"excellent", "good"}
    assert short_cpu_spike.score >= 80
    assert abs(sum(healthy.weights.values()) - 1.0) < 0.0001


def main() -> None:
    test_action_contexts()
    test_smart_command_flows()
    test_health_model()
    test_server_score()
    print("control-center architecture smoke: ok")


if __name__ == "__main__":
    main()
