"""Batch8-paced replay with device timestamps independent of host scheduling."""

from __future__ import annotations

from pathlib import Path
import threading
import time

import numpy as np

from wifiguard_edge.csi.protocol import CsiFrame


class LocalReplaySource(threading.Thread):
    is_replay = True
    def __init__(self, buffer, source="synthetic"):
        super().__init__(daemon=True, name="local-csi-replay")
        self.buffer = buffer
        self.source = source
        self.stop_event = threading.Event()
        self.packet_count = 0
        self.error = None
        self.recording = None
        self.loop_seconds = None
        if source != "synthetic":
            with np.load(Path(source), allow_pickle=False) as archive:
                times = np.asarray(archive["times"], dtype=np.float64)
                amps = np.asarray(archive["amps"], dtype=np.float32)
            if (
                times.ndim != 1
                or len(times) < 2
                or amps.ndim != 2
                or amps.shape[0] != len(times)
                or amps.shape[1] not in (30, 245)
            ):
                raise ValueError("recording requires times and amps (N,30|245)")
            if not np.isfinite(times).all() or not np.isfinite(amps).all():
                raise ValueError("recording must be finite")
            if (
                np.any(np.diff(times) <= 0)
                or not 300 <= 1 / np.median(np.diff(times)) <= 340
            ):
                raise ValueError(
                    "recording timestamps must be increasing at about 320 Hz"
                )
            self.recording = times - times[0], amps
            self.loop_seconds = float(times[-1] - times[0] + np.median(np.diff(times)))

    def stop(self):
        self.stop_event.set()

    @property
    def running(self):
        return self.is_alive() and not self.stop_event.is_set()

    def status(self):
        return {
            "connected": self.running,
            "frames_ok": self.packet_count,
            "source": self.source,
            "error": self.error,
            "source_clock_hz": 320,
        }

    def run(self):
        started = time.monotonic()
        channels = np.arange(245) / 490
        try:
            while not self.stop_event.is_set():
                if self.recording:
                    times, amps = self.recording
                    pos = self.packet_count % len(times)
                    loop = self.packet_count // len(times)
                    device_time = float(times[pos] + loop * self.loop_seconds)
                    values = amps[pos]
                else:
                    device_time = self.packet_count / 320
                    burst = np.exp(-((((device_time % 6) - 3) / 0.15) ** 2)) * np.sin(
                        2 * np.pi * 16 * device_time
                    )
                    values = (
                        20
                        + np.sin(2 * np.pi * (0.8 + channels) * device_time)
                        + burst * (0.2 + channels)
                    ).astype(np.float32)
                # Sleep once per eight frames, like real 25 ms WGSP batches.
                if self.packet_count % 8 == 0 and self.stop_event.wait(
                    max(0, started + device_time - time.monotonic())
                ):
                    break
                self.buffer.append(
                    CsiFrame(
                        seq=self.packet_count,
                        mac="00:00:00:00:00:00",
                        rssi=-45,
                        noise_floor=-92,
                        channel=48,
                        timestamp_us=round(device_time * 1e6) & 0xFFFFFFFF,
                        fft_gain=0,
                        agc_gain=0,
                        csi_len=len(values) * 2,
                        amps=values,
                        host_time=time.monotonic(),
                    )
                )
                self.packet_count += 1
        except Exception as exc:
            self.error = str(exc)


class RawWgspReplaySource(LocalReplaySource):
    """Synthetic signed-int8 RAW -> real WGSP CRC decode/amplitude/buffer.

    No SPI clocks/GPIO/ESP32 are simulated. Fixture generation is timed and is
    an additional conservative PC-test cost; real ESP32 does that work upstream.
    """
    def __init__(self, buffer, *, scenario_file=None):
        super().__init__(buffer)
        self.scenario_file = Path(scenario_file) if scenario_file else None
        self.scenario = "motion"
        self.source = "synthetic-wgsp"
        self.generation_seconds = self.decode_seconds = 0.0
        self.batches = 0
        self.frames_decoded = 0

    def status(self):
        return {**super().status(), "frames_ok": self.frames_decoded, "raw_wire_decode": True,
                "protocol_rejects": 0, "generation_cpu_fixture_included": True,
                "fixture_generation_wall_seconds": self.generation_seconds,
                "decode_amplitude_buffer_wall_seconds": self.decode_seconds,
                "raw_batches": self.batches, "simulation_scenario": self.scenario if self.scenario_file else None}

    def run(self):
        from wifiguard_edge.config import TransportConfig
        from wifiguard_edge.transport.wgsp_protocol import (
            FLAG_CSI_VALID, SpiCsiFrame, decode_spi_csi_frame,
            encode_spi_csi_frame, profile_fingerprint,
        )
        from wifiguard_edge.transport.wgsp_source import WgspBatch8Source

        decoder = WgspBatch8Source(self.buffer, TransportConfig(kind="spi"))
        channels = np.arange(245, dtype=np.float64) / 490
        fingerprint = profile_fingerprint("esp32c5-he20-5g-ch48-amfall-spi-v1")
        started = time.monotonic()
        try:
            while not self.stop_event.is_set():
                if self.stop_event.wait(max(0, started + self.packet_count / 320 - time.monotonic())):
                    break
                if self.scenario_file:
                    from .control import read_json
                    self.scenario = read_json(self.scenario_file).get("scenario", "empty")
                    if self.scenario == "disconnect":
                        # Device clock advances during lost reception; never
                        # backfill artificial CSI after a reconnect.
                        self.packet_count = int((time.monotonic() - started) * 320 // 8) * 8
                        self.stop_event.wait(.025)
                        continue
                tick = time.perf_counter()
                packets = []
                for seq in range(self.packet_count, self.packet_count + 8):
                    t = seq / 320
                    burst = np.exp(-((((t % 6) - 3) / .15) ** 2)) * np.sin(2 * np.pi * 16 * t)
                    amplitude = 30 + 8 * np.sin(2 * np.pi * (.8 + channels) * t) + burst * (5 + 10 * channels)
                    phase = .4 * np.sin(2 * np.pi * .3 * t + channels)
                    if self.scenario_file and self.scenario in ("empty", "still"):
                        # A fixture signal, NOT a forced presence/model decision.
                        amplitude = np.full_like(channels, 30.0) if self.scenario == "empty" else 30 + 1.5 * np.sin(2 * np.pi * .25 * t + channels)
                        phase = np.zeros_like(channels)
                    iq = np.stack([amplitude * np.cos(phase), amplitude * np.sin(phase)], axis=1)
                    packets.append(encode_spi_csi_frame(SpiCsiFrame(
                        stream_epoch=1, frame_seq=seq, source_timestamp_us=round(t * 1e6),
                        source_dropped_total=0, queue_dropped_total=0, spi_dropped_total=0,
                        transport_errors_total=0, channel=48, bandwidth_mhz=20,
                        rssi=-45, noise_floor=-92, agc_gain=0, fft_gain=0, rx_format=4,
                        flags=FLAG_CSI_VALID, profile_fingerprint=fingerprint,
                        csi_iq=np.rint(iq).astype(np.int8))))
                batch = b"".join(packets)
                self.generation_seconds += time.perf_counter() - tick
                tick = time.perf_counter()
                for offset in range(0, len(batch), 576):
                    decoder._append(decode_spi_csi_frame(batch[offset:offset + 576]))
                self.decode_seconds += time.perf_counter() - tick
                self.packet_count += 8
                self.frames_decoded += 8
                self.batches += 1
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
