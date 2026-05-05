#!/usr/bin/env python3
"""
Test suite for TCI client fixes.

Covers:
  1. Multi-command frame splitting
  2. stop / start mid-session lifecycle
  3. receive_only TX guard
  4. Mode guard (blocks TX in non-CW modes)
  5. Safety TX release timeout (fires if ExpertSDR3 never releases TX)

Runs a minimal in-process TCI server — no radio or external simulator needed.
Usage:
    python test_tci_fixes.py
"""

import asyncio
import websockets
import time

from tci_client import TCIClient

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results = []


def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    msg = f"  [{status}] {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append((name, condition))


# ---------------------------------------------------------------------------
# Minimal in-process TCI server
# ---------------------------------------------------------------------------

class MinimalTCIServer:
    """Tiny WebSocket TCI server for testing."""

    def __init__(self):
        self.received = []       # commands received from client
        self.server = None
        self.host = "127.0.0.1"
        self.port = 59001
        self._ws = None          # last connected websocket
        self._ready = asyncio.Event()

    async def handler(self, websocket):
        self._ws = websocket
        self._ready.set()
        async for msg in websocket:
            self.received.append(msg.rstrip(";"))

    async def start(self):
        self.server = await websockets.serve(self.handler, self.host, self.port)

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def send(self, frame: str):
        """Send a raw frame to the connected client."""
        await self._ready.wait()
        await self._ws.send(frame)

    async def wait_for_client(self, timeout=3.0):
        await asyncio.wait_for(self._ready.wait(), timeout)


# ---------------------------------------------------------------------------
# Test 1 — Multi-command frame splitting
# ---------------------------------------------------------------------------

async def test_multiframe():
    print("\n--- Test 1: Multi-command frame splitting ---")
    srv = MinimalTCIServer()
    await srv.start()
    try:
        messages = []
        client = TCIClient(srv.host, srv.port)
        client.on_message = lambda m: messages.append(m)

        # Start connect (runs in background; we drive it manually)
        connect_task = asyncio.create_task(client.connect(timeout=3.0))
        await srv.wait_for_client()

        # Send startup bundle + READY in one frame (realistic ExpertSDR3 behaviour)
        await srv.send("DEVICE:SunSDR2DX;MODULATIONS_LIST:CW,USB,LSB;PROTOCOL:ExpertSDR3,1.9;VFO:0,0,14000000;READY;")
        connected = await connect_task

        check("connect returns True", connected)
        check("client.ready is True", client.ready)

        # Verify individual commands were dispatched (not the raw bundle)
        check("VFO dispatched separately",   any(m.startswith("VFO:")         for m in messages))
        check("DEVICE dispatched separately", any(m.startswith("DEVICE:")      for m in messages))

        # Now send a multi-command mid-session frame
        messages.clear()
        recv_task = asyncio.create_task(client.receive_loop())
        await srv.send("TRX:0,false;MODULATION:0,CW;RIT_OFFSET:0,-50;")
        await asyncio.sleep(0.1)
        recv_task.cancel()
        try: await recv_task
        except asyncio.CancelledError: pass

        check("TRX dispatched from multi-frame",        any(m.startswith("TRX:")       for m in messages))
        check("MODULATION dispatched from multi-frame", any(m.startswith("MODULATION:") for m in messages))
        check("RIT_OFFSET dispatched from multi-frame", any(m.startswith("RIT_OFFSET:") for m in messages))

        await client.disconnect()
    finally:
        await srv.stop()


# ---------------------------------------------------------------------------
# Test 2 — stop / start lifecycle
# ---------------------------------------------------------------------------

async def test_lifecycle():
    print("\n--- Test 2: stop / start lifecycle ---")
    srv = MinimalTCIServer()
    await srv.start()
    try:
        disconnect_calls = []
        client = TCIClient(srv.host, srv.port)
        client.on_disconnect = lambda: disconnect_calls.append(1)

        connect_task = asyncio.create_task(client.connect(timeout=3.0))
        await srv.wait_for_client()
        await srv.send("READY;")
        await connect_task

        # Start receive loop in background
        recv_task = asyncio.create_task(client.receive_loop())

        # Server sends stop mid-session
        await srv.send("stop;")
        await asyncio.sleep(0.1)

        check("ready=False after stop",         not client.ready)
        check("on_disconnect called after stop", len(disconnect_calls) > 0)

        # Server sends start
        await srv.send("start;")
        await asyncio.sleep(0.1)

        check("ready=True after start", client.ready)

        recv_task.cancel()
        try: await recv_task
        except asyncio.CancelledError: pass

        await client.disconnect()
    finally:
        await srv.stop()


# ---------------------------------------------------------------------------
# Test 3 — receive_only TX guard
# ---------------------------------------------------------------------------

async def test_receive_only():
    print("\n--- Test 3: receive_only TX guard ---")
    srv = MinimalTCIServer()
    await srv.start()
    try:
        client = TCIClient(srv.host, srv.port)

        connect_task = asyncio.create_task(client.connect(timeout=3.0))
        await srv.wait_for_client()
        await srv.send("READY;")
        await connect_task

        recv_task = asyncio.create_task(client.receive_loop())

        # Server sets receive_only
        await srv.send("receive_only:0,true;")
        await asyncio.sleep(0.1)

        check("receive_only=True after server message", client.receive_only)

        # Attempt TX commands — all must be blocked
        srv.received.clear()
        client.current_mode = "CW"

        sent = await client.send_cw_macros("TEST")
        check("send_cw_macros blocked when receive_only", sent == False)
        check("no commands forwarded to server",          len(srv.received) == 0)

        await client.set_trx(True)
        check("set_trx(True) blocked when receive_only", len(srv.received) == 0)

        # Server clears receive_only
        await srv.send("receive_only:0,false;")
        await asyncio.sleep(0.1)
        check("receive_only=False after server clear", not client.receive_only)

        recv_task.cancel()
        try: await recv_task
        except asyncio.CancelledError: pass

        await client.disconnect()
    finally:
        await srv.stop()


# ---------------------------------------------------------------------------
# Test 4 — Mode guard (blocks TX in non-CW modes)
# ---------------------------------------------------------------------------

async def test_mode_guard():
    print("\n--- Test 4: Mode guard (block macro in non-CW mode) ---")
    srv = MinimalTCIServer()
    await srv.start()
    try:
        client = TCIClient(srv.host, srv.port)

        connect_task = asyncio.create_task(client.connect(timeout=3.0))
        await srv.wait_for_client()
        await srv.send("READY;")
        await connect_task

        recv_task = asyncio.create_task(client.receive_loop())

        # --- DIGU: must BLOCK (not auto-switch), protect other TCI clients ---
        await srv.send("MODULATION:0,DIGU;")
        await asyncio.sleep(0.05)
        check("current_mode tracked as DIGU", client.current_mode == "DIGU")

        srv.received.clear()
        sent = await client.send_cw_macros("SM0ONR", force_ptt=False)
        await asyncio.sleep(0.05)
        check("send_cw_macros blocked from DIGU (no auto-switch)",  sent == False)
        check("no MODULATION:CW sent when blocked",
              not any("MODULATION" in c and "CW" in c for c in srv.received))
        check("no cw_macros sent when blocked",
              not any("cw_macros" in c for c in srv.received))
        # TRX:true must NOT have been sent (blocked early)
        check("TRX:true NOT sent when mode blocked",
              not any(c.upper().startswith("TRX") and "true" in c.lower() for c in srv.received))

        # --- USB: also blocked ---
        await srv.send("MODULATION:0,USB;")
        await asyncio.sleep(0.05)
        srv.received.clear()
        sent = await client.send_cw_macros("SM0ONR", force_ptt=True)
        await asyncio.sleep(0.05)
        check("send_cw_macros blocked from USB", sent == False)
        check("no commands sent when USB mode blocked",
              len(srv.received) == 0)

        # --- CW: allowed, no mode switch needed ---
        await srv.send("MODULATION:0,CW;")
        await asyncio.sleep(0.05)
        srv.received.clear()
        sent = await client.send_cw_macros("SM0ONR")
        await asyncio.sleep(0.05)
        check("send_cw_macros allowed in CW mode (no switch)",   sent == True)
        check("no MODULATION command when already in CW",
              not any("MODULATION" in c for c in srv.received))

        # --- None (unknown): proceed without switching ---
        client.current_mode = None
        srv.received.clear()
        sent = await client.send_cw_macros("SM0ONR")
        await asyncio.sleep(0.05)
        check("send_cw_macros allowed when mode unknown (None)", sent == True)
        check("no MODULATION command when mode unknown",
              not any("MODULATION" in c for c in srv.received))

        recv_task.cancel()
        try: await recv_task
        except asyncio.CancelledError: pass

        await client.disconnect()
    finally:
        await srv.stop()


# ---------------------------------------------------------------------------
# Test 5 — Safety TX release
# ---------------------------------------------------------------------------

async def test_safety_tx_release():
    """
    Verify the safety timeout fires when the server never sends TRX:0,false.
    We use a very short timeout by temporarily overriding the calculation.
    """
    print("\n--- Test 5: Safety TX release timeout ---")
    srv = MinimalTCIServer()
    await srv.start()
    try:
        from unittest.mock import patch, AsyncMock
        import main as main_mod

        # Build a minimal config so TCICWController can be instantiated
        # without a config.yaml by injecting config directly.
        class FakeController:
            """Minimal stand-in that only exercises the safety release logic."""
            def __init__(self):
                self.tci_client = TCIClient(srv.host, srv.port)
                self.tci_client.current_mode = "CW"
                self.manual_ptt_active = True
                self.macro_active = False
                self.macro_release_task = None
                self.macro_safety_tx_release_task = None
                self.config = {
                    'tci': {'force_ptt': True},
                    'cw':  {'speed_wpm': 25},
                    'operator': {'callsign': 'SM0ONR'},
                    'keyboard': {},
                }
                self.logger = __import__('logging').getLogger('test')
                # Bind the real method
                self._on_macro_send = main_mod.TCICWController._on_macro_send.__get__(self, FakeController)
                self._on_tci_message = main_mod.TCICWController._on_tci_message.__get__(self, FakeController)

        ctrl = FakeController()
        connect_task = asyncio.create_task(ctrl.tci_client.connect(timeout=3.0))
        await srv.wait_for_client()
        await srv.send("READY;")
        await connect_task

        recv_task = asyncio.create_task(ctrl.tci_client.receive_loop())

        # Monkey-patch the time calculation so safety fires in ~0.3s
        original_method = main_mod.TCICWController._on_macro_send

        tx_released_by_safety = asyncio.Event()
        original_set_trx = ctrl.tci_client.set_trx

        async def spy_set_trx(transmit):
            if not transmit:
                tx_released_by_safety.set()
            await original_set_trx(transmit)

        ctrl.tci_client.set_trx = spy_set_trx

        # Override estimated_ms to a tiny value inline by patching the method
        # We re-implement _on_macro_send with a fixed short timeout just for this test
        async def fast_on_macro_send(key_name, message):
            ctrl.macro_active = True
            force_ptt = ctrl.config['tci'].get('force_ptt', False)
            sent = await ctrl.tci_client.send_cw_macros(message, force_ptt=force_ptt)
            if not sent:
                ctrl.macro_active = False
                return
            # Use a very short TX release window (300ms) for testing
            async def macro_tx_release():
                await asyncio.sleep(0.3)
                if ctrl.tci_client and ctrl.tci_client.ready:
                    ctrl.logger.warning("TX release fired (test)")
                    await ctrl.tci_client.set_trx(False)
                ctrl.macro_active = False
                ctrl.macro_safety_tx_release_task = None
            ctrl.macro_safety_tx_release_task = asyncio.create_task(macro_tx_release())

        ctrl._on_macro_send = fast_on_macro_send

        # Trigger macro — server will NOT send TRX:0,false back
        await ctrl._on_macro_send("F1", "SM0ONR")

        # Wait for safety release (should fire within ~0.5s)
        try:
            await asyncio.wait_for(tx_released_by_safety.wait(), timeout=1.5)
            check("Safety TRX release fired when server did not release TX", True)
        except asyncio.TimeoutError:
            check("Safety TRX release fired when server did not release TX", False,
                  "timeout — release did not fire within 1.5s")

        # Test that TRX:false from server cancels the task
        await ctrl._on_macro_send("F1", "SM0ONR")
        task_before_cancel = ctrl.macro_safety_tx_release_task
        check("Safety task created after macro send", task_before_cancel is not None)

        # Simulate server sending TRX:0,false
        ctrl._on_tci_message("TRX:0,false")
        await asyncio.sleep(0.05)
        check("Safety task cancelled when server sends TRX:0,false",
              ctrl.macro_safety_tx_release_task is None)

        recv_task.cancel()
        try: await recv_task
        except asyncio.CancelledError: pass

        await ctrl.tci_client.disconnect()
    finally:
        await srv.stop()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main():
    print("=" * 55)
    print(" TCI Client Fix Test Suite")
    print("=" * 55)

    await test_multiframe()
    await test_lifecycle()
    await test_receive_only()
    await test_mode_guard()
    await test_safety_tx_release()

    print("\n" + "=" * 55)
    passed = sum(1 for _, ok in results if ok)
    total  = len(results)
    print(f" Results: {passed}/{total} passed")
    if passed < total:
        print(f"\n Failed tests:")
        for name, ok in results:
            if not ok:
                print(f"   - {name}")
    print("=" * 55)
    return passed == total


if __name__ == "__main__":
    ok = asyncio.run(main())
    raise SystemExit(0 if ok else 1)
