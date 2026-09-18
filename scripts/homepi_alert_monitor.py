from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from dotenv import load_dotenv

from services.health_checks import HealthResult, check_systemd_service
from services.phone_alerts import (
    AlertGate,
    AlertStateStore,
    AsteriskCallFileDialer,
    LocalSpeechRenderer,
    PhoneAlertConfig,
    collect_alert_signals,
    compose_voice_message,
)
from services.system_metrics import SystemMetricsSampler

logger = logging.getLogger("homepi.phone_alerts")


async def _check_services(units: tuple[str, ...]) -> list[HealthResult]:
    if not units:
        return []
    return list(
        await asyncio.gather(
            *(check_systemd_service(unit, unit) for unit in units)
        )
    )


async def _sleep_or_stop(stop_event: asyncio.Event, seconds: int) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(1, seconds))
    except TimeoutError:
        pass


async def run() -> None:
    load_dotenv(Path(".env.alerts"))
    config = PhoneAlertConfig.from_env()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    if not config.enabled:
        logger.info(
            "HomePi phone alerts are disabled. Set HOMEPI_ALERTS_ENABLED=true to activate."
        )
        await stop_event.wait()
        return

    config.validate_for_calling()
    state = AlertStateStore(config.state_dir)
    gate = AlertGate(
        confirm_seconds=config.confirm_seconds,
        min_alert_interval_seconds=config.min_alert_interval_seconds,
        state=state,
    )
    renderer = LocalSpeechRenderer(config)
    dialer = AsteriskCallFileDialer(config)
    sampler = SystemMetricsSampler(
        interval_seconds=min(config.check_interval_seconds, 30)
    )

    logger.info(
        "HomePi phone alerts active; monitoring %d services every %ds",
        len(config.monitored_services),
        config.check_interval_seconds,
    )

    await sampler.start()
    try:
        while not stop_event.is_set():
            try:
                metrics, services = await asyncio.gather(
                    sampler.get(),
                    _check_services(config.monitored_services),
                )
                signals = collect_alert_signals(metrics, services, config)
                due = gate.due(signals)

                if due:
                    logger.warning(
                        "Critical HomePi alert confirmed: %s",
                        "; ".join(signal.summary for signal in due),
                    )
                    message = compose_voice_message(due)
                    audio_file = await asyncio.to_thread(renderer.render, message)
                    queued = await asyncio.to_thread(dialer.queue, audio_file)
                    gate.mark_alerted(due)
                    logger.warning(
                        "Automatic phone alert queued: %s",
                        queued.name,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Do not terminate the watchdog because a temporary SIP/TTS/spool
                # problem would otherwise disable the very alert path we need.
                logger.exception("HomePi phone-alert cycle failed")

            await _sleep_or_stop(stop_event, config.check_interval_seconds)
    finally:
        await sampler.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
