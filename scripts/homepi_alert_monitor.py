from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import replace
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
from services.phone_incidents import (
    IncidentStore,
    InteractiveAlertConfig,
    InteractiveAsteriskDialer,
    process_action_requests,
    restart_unit_for_signals,
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


async def _sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0.1, seconds))
    except TimeoutError:
        pass


async def _interactive_action_worker(
    stop_event: asyncio.Event,
    config: InteractiveAlertConfig,
    store: IncidentStore,
) -> None:
    while not stop_event.is_set():
        try:
            acknowledgements = store.consume_acknowledgements()
            if acknowledgements:
                logger.info(
                    "Processed %d phone acknowledgement(s)",
                    acknowledgements,
                )
            processed = await process_action_requests(config, store)
            if processed:
                logger.info(
                    "Processed %d interactive phone action(s)",
                    processed,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Interactive phone action worker failed")
        await _sleep_or_stop(stop_event, 1.0)


async def run() -> None:
    load_dotenv(Path(".env.alerts"))
    config = PhoneAlertConfig.from_env()
    interactive = InteractiveAlertConfig.from_env()

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
    sampler = SystemMetricsSampler(
        interval_seconds=min(config.check_interval_seconds, 30)
    )

    incident_store: IncidentStore | None = None
    interactive_dialer: InteractiveAsteriskDialer | None = None
    action_worker: asyncio.Task[None] | None = None

    if interactive.enabled:
        incident_store = IncidentStore(interactive)
        incident_store.ensure_directories()
        interactive_phone = replace(config, audio_dir=interactive.audio_dir)
        renderer = LocalSpeechRenderer(interactive_phone)
        interactive_dialer = InteractiveAsteriskDialer(
            interactive_phone,
            interactive,
            incident_store,
        )
        action_worker = asyncio.create_task(
            _interactive_action_worker(
                stop_event,
                interactive,
                incident_store,
            ),
            name="homepi-phone-actions",
        )
        logger.info(
            "Interactive phone assistant active; escalation=%ds max=%d",
            interactive.escalation_seconds,
            interactive.max_escalations,
        )
    else:
        renderer = LocalSpeechRenderer(config)

    fallback_dialer = AsteriskCallFileDialer(config)

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
                current_signal_keys = {signal.key for signal in signals}
                due = gate.due(signals)

                if due:
                    logger.warning(
                        "Critical HomePi alert confirmed: %s",
                        "; ".join(signal.summary for signal in due),
                    )
                    message = compose_voice_message(due)
                    audio_file = await asyncio.to_thread(
                        renderer.render,
                        message,
                    )

                    if (
                        interactive.enabled
                        and incident_store is not None
                        and interactive_dialer is not None
                    ):
                        restart_unit = restart_unit_for_signals(
                            due,
                            interactive.restartable_services,
                        )
                        incident = incident_store.create(
                            due,
                            audio_file=audio_file,
                            restart_unit=restart_unit,
                        )
                        queued = await asyncio.to_thread(
                            interactive_dialer.queue,
                            incident,
                        )
                    else:
                        queued = await asyncio.to_thread(
                            fallback_dialer.queue,
                            audio_file,
                        )

                    gate.mark_alerted(due)
                    logger.warning(
                        "Automatic phone alert queued: %s",
                        queued.name,
                    )

                if (
                    interactive.enabled
                    and incident_store is not None
                    and interactive_dialer is not None
                ):
                    for incident in incident_store.reconcile(
                        current_signal_keys
                    ):
                        queued = await asyncio.to_thread(
                            interactive_dialer.queue,
                            incident,
                        )
                        logger.warning(
                            "Escalated unacknowledged incident %s via %s",
                            incident.id,
                            queued.name,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                # The watchdog must stay alive through temporary SIP, TTS or
                # spool failures. A later cycle can recover automatically.
                logger.exception("HomePi phone-alert cycle failed")

            await _sleep_or_stop(
                stop_event,
                float(config.check_interval_seconds),
            )
    finally:
        stop_event.set()
        if action_worker is not None:
            action_worker.cancel()
            try:
                await action_worker
            except asyncio.CancelledError:
                pass
        await sampler.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
