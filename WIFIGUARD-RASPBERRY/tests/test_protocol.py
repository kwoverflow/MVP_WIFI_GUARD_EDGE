#!/usr/bin/env python3
"""Test protocol implementation"""

from wifiguard_edge.transport import protocol


def test_espnow():
    print("=== ESP-NOW Protocol ===")
    cmd = protocol.EspnowCmdPacket(cmd=protocol.EspnowCmd.SET_RATE, pps=320)
    packed = cmd.pack()
    print(f"Packed command: {packed.hex()}")
    unpacked = protocol.EspnowCmdPacket.unpack(packed)
    print(f"Unpacked: pkt_type={unpacked.pkt_type}, cmd={unpacked.cmd}, pps={unpacked.pps}")
    assert unpacked.pps == 320
    print("[PASS] ESP-NOW test passed\n")

def test_spi_header():
    print("=== SPI Frame Header ===")
    hdr = protocol.SpiFrameHdr(
        magic=protocol.SPI_MAGIC_RX_TO_RPi,
        mode=protocol.SpiMode.TRAIN,
        pkt_type=protocol.SpiPktType.TRAIN_PROGRESS,
        payload_len=100
    )
    hdr_packed = hdr.pack()
    print(f"Packed header: {hdr_packed.hex()}")
    hdr_unpacked = protocol.SpiFrameHdr.unpack(hdr_packed)
    print(f"Unpacked: magic={hdr_unpacked.magic.hex()}, mode={hdr_unpacked.mode}, pkt_type={hdr_unpacked.pkt_type}")
    assert hdr_unpacked.payload_len == 100
    print("[PASS] SPI header test passed\n")

def test_json():
    print("=== JSON Payloads ===")
    json_progress = protocol.create_train_progress_json(5, 20, 250, -58.2)
    print(f"Train progress: {json_progress}")

    json_done = protocol.create_train_done_json(-58.5, 0.72, 1000)
    print(f"Train done: {json_done}")

    json_status = protocol.create_occupancy_status_json(
        ts_ms=1751080000000, entry=True, motion=False,
        motion_count_3s=2, rssi=-57.1, rssi_variance=0.85,
        base_rssi_mean=-58.5, base_rssi_var=0.72,
        jitter=0.04, jitter_threshold=0.08,
        occupancy_confirmed=False, req_mode_change="fall"
    )
    print(f"Occupancy status: {json_status}")
    print("[PASS] JSON test passed\n")

def test_csi_frame():
    print("=== CSI Raw Frame ===")
    csi_data = bytes(range(50))
    csi_frame = protocol.CsiRawFrame(
        seq=123, timestamp_ms=1234567890,
        rssi=-55, noise_floor=100,
        csi_len=50, drop_count=0,
        csi_data=csi_data
    )
    csi_packed = csi_frame.pack()
    print(f"CSI frame size: {len(csi_packed)} bytes (expected 140)")
    assert len(csi_packed) == 140
    csi_unpacked = protocol.CsiRawFrame.unpack(csi_packed)
    print(f"Unpacked CSI: seq={csi_unpacked.seq}, rssi={csi_unpacked.rssi}, csi_len={csi_unpacked.csi_len}")
    assert csi_unpacked.seq == 123
    assert csi_unpacked.rssi == -55
    print("[PASS] CSI frame test passed\n")

def test_spi_frame():
    print("=== SPI Frame Wrapper ===")
    json_progress = protocol.create_train_progress_json(5, 20, 250, -58.2)
    spi_hdr = protocol.SpiFrameHdr(
        mode=protocol.SpiMode.TRAIN,
        pkt_type=protocol.SpiPktType.TRAIN_PROGRESS,
        payload_len=len(json_progress)
    )
    spi_frame = protocol.SpiFrame(spi_hdr, json_progress.encode('utf-8'))
    frame_packed = spi_frame.pack(protocol.SPI_FRAME_SIZE_SMALL)
    print(f"SPI frame size: {len(frame_packed)} bytes (expected {protocol.SPI_FRAME_SIZE_SMALL})")
    assert len(frame_packed) == protocol.SPI_FRAME_SIZE_SMALL
    frame_unpacked, payload_len = protocol.SpiFrame.unpack(frame_packed)
    print(f"Unpacked frame: mode={frame_unpacked.hdr.mode}, pkt_type={frame_unpacked.hdr.pkt_type}, payload_len={payload_len}")
    print(f"Payload: {frame_unpacked.payload[:payload_len].decode('utf-8')}")
    print("[PASS] SPI frame test passed\n")

if __name__ == '__main__':
    test_espnow()
    test_spi_header()
    test_json()
    test_csi_frame()
    test_spi_frame()
    print("[SUCCESS] All protocol tests passed!")
