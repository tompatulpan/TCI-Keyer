#!/usr/bin/env python3
"""
USB Paddle Handler for Manual CW Keying

Reads XIAO SAMD21 USB HID paddle input and sends keying commands to TCI
with accurate timing and local sidetone.
"""

import asyncio
import time
import logging
from typing import Optional, Callable
from xiao_hid_reader import XiaoHIDReader


class USBPaddleHandler:
    """Handle USB paddle input with timing measurement"""
    
    def __init__(self, device_path: Optional[str] = None, 
                 vendor_id: str = "2886", product_id: str = "802f",
                 keyer_mode: str = "straight", wpm: int = 20,
                 debug: bool = False,
                 use_vail_firmware: bool = False):
        """
        Initialize USB paddle handler
        
        Args:
            device_path: Explicit device path or None for auto-detect
            vendor_id: USB Vendor ID
            product_id: USB Product ID
            keyer_mode: 'straight', 'iambic-a', or 'iambic-b' (ignored if use_vail_firmware=True)
            wpm: Words per minute for iambic keyer (ignored if use_vail_firmware=True)
            debug: Enable debug output
            use_vail_firmware: If True, expects Vail adapter firmware to handle keying logic
                              Python will treat output as "straight key" mode (measure timing only)
        """
        self.hid_reader = XiaoHIDReader(
            device_path=device_path,
            vid=vendor_id,
            pid=product_id,
            debug=debug
        )
        
        self.running = False
        self.poll_interval = 0.001  # 1ms polling
        self.keyer_mode = keyer_mode.lower()
        self.use_vail_firmware = use_vail_firmware
        
        # Initialize logger first (needed for messages below)
        self.logger = logging.getLogger("USBPaddleHandler")
        
        # Initialize iambic keyer if needed (NOT used with Vail firmware)
        self.iambic_keyer = None
        if not use_vail_firmware and self.keyer_mode in ['iambic-a', 'iambic-b']:
            mode_letter = 'B' if self.keyer_mode == 'iambic-b' else 'A'
            self.iambic_keyer = IambicKeyer(wpm=wpm, mode=mode_letter)
        elif use_vail_firmware:
            self.logger.info("Using Vail adapter firmware for keyer logic (Python measures timing only)")
        
        # Timing state (for straight key mode)
        self.key_down = False
        self.last_event_time: Optional[float] = None
        self.transmission_start: Optional[float] = None
        
        # Callbacks
        self.on_key_event: Optional[Callable[[bool, int], None]] = None  # (key_down, prev_duration_ms)
        self.on_tx_start: Optional[Callable[[], None]] = None  # Called when keying starts (for TX pre-arm)
    
    def connect(self) -> bool:
        """
        Connect to USB HID device
        
        Returns:
            True if successful, False otherwise
        """
        success = self.hid_reader.connect()
        if success:
            self.logger.info("USB paddle connected")
        return success
    
    def set_wpm(self, wpm: int):
        """
        Set keyer speed in WPM (for iambic mode)
        
        Args:
            wpm: Words per minute (15-40)
        """
        if self.iambic_keyer:
            self.iambic_keyer.set_speed(wpm)
            self.logger.info(f"Iambic keyer speed set to {wpm} WPM")
        else:
            self.logger.debug(f"set_wpm called but no iambic keyer active (mode: {self.keyer_mode})")
    
    
    def disconnect(self):
        """Disconnect from USB HID device"""
        self.hid_reader.close()
        self.logger.info("USB paddle disconnected")
    
    async def poll_loop(self):
        """
        Main polling loop - reads paddle state and generates keying events
        
        Supports:
        - straight: Either paddle = key down (no timing logic)
        - iambic-a/b: Full iambic keyer with squeeze and memory (Python implementation)
        - vail firmware: Firmware handles keying, Python measures timing (uses straight mode)
        """
        self.running = True
        
        if self.use_vail_firmware:
            mode_display = "VAIL FIRMWARE (Python measures timing only)"
        else:
            mode_display = self.keyer_mode.upper().replace('-', ' ')
        
        self.logger.info(f"USB paddle polling started ({mode_display} mode)")
        
        # Choose appropriate loop based on mode
        # Note: Vail firmware mode uses straight_poll_loop (firmware already generated timing)
        if self.iambic_keyer:
            await self._iambic_poll_loop()
        else:
            await self._straight_poll_loop()
        
        self.logger.info("USB paddle polling stopped")
    
    async def _straight_poll_loop(self):
        """
        Straight key mode: either paddle = key down
        
        Also used for Vail firmware mode - in that case, firmware has already
        generated perfect iambic timing, and Python just measures what comes out
        as Ctrl key presses (appears as "straight key" from Python's perspective).
        
        Uses the same TX pre-arming approach as iambic mode for reliability.
        """
        # Timing state for TCI protocol
        last_event_time = time.time()
        transmission_start = None
        
        # Get sidetone reference (set by main.py)
        sidetone = getattr(self, '_sidetone', None)
        
        # Disconnection callback
        on_disconnect = getattr(self, 'on_disconnect', None)
        
        while self.running:
            try:
                # Read paddle states (dit, dah)
                dit, dah = self.hid_reader.read_paddles()
                
                # Combine for straight key mode (either paddle = key down)
                new_key_down = dit or dah
                
                # Detect state change
                if new_key_down != self.key_down:
                    now = time.time()
                    
                    # Update sidetone IMMEDIATELY
                    if sidetone:
                        sidetone.set_key(new_key_down)
                    
                    # Calculate duration of PREVIOUS state (TCI protocol semantics)
                    if transmission_start is None:
                        transmission_start = now
                        previous_duration_ms = 0
                    else:
                        previous_duration_ms = int((now - last_event_time) * 1000)
                    
                    # Cap duration to prevent overflow
                    previous_duration_ms = min(previous_duration_ms, 65535)
                    
                    # Update state
                    last_event_time = now
                    self.key_down = new_key_down
                    
                    # Send keying event with PREVIOUS state's duration
                    if self.on_key_event:
                        await self.on_key_event(new_key_down, previous_duration_ms)
                    
                    # Reset timing after idle period
                    if not new_key_down:
                        # Schedule reset after 2 seconds of idle
                        await asyncio.sleep(0.001)  # Brief check interval
                        continue
                
                # Reset state after prolonged idle (5 seconds)
                if not new_key_down and transmission_start:
                    if (time.time() - last_event_time) > 5.0:
                        transmission_start = None
                        self.logger.debug("Straight key: Reset after idle")
                
                # Sleep to achieve polling rate
                await asyncio.sleep(self.poll_interval)
                
            except OSError as e:
                # Device disconnected
                self.logger.warning(f"USB paddle disconnected: {e}")
                self.running = False
                if on_disconnect:
                    await on_disconnect()
                break
            except Exception as e:
                self.logger.error(f"Error in straight key poll loop: {e}")
                await asyncio.sleep(0.1)
    
    async def _iambic_poll_loop(self):
        """Iambic keyer mode: generates dit/dah elements with proper timing"""
        
        # Timing state for TCI protocol
        last_event_time = time.time()
        transmission_start = None
        
        # Get sidetone reference from callback context (set by main.py)
        sidetone = getattr(self, '_sidetone', None)
        
        # Disconnection callback
        on_disconnect = getattr(self, 'on_disconnect', None)
        
        def paddle_reader():
            """Read current paddle states - SYNCHRONOUS for precise timing"""
            return self.hid_reader.read_paddles()
        
        async def send_element(key_down: bool, duration_ms: float):
            """Send a single keying element
            
            Args:
                key_down: True for DOWN, False for UP
                duration_ms: Element duration (dit/dah/space) - used for logging only
            """
            nonlocal last_event_time, transmission_start
            
            now = time.time()
            
            # Update sidetone IMMEDIATELY (synchronous, no delay)
            if sidetone:
                sidetone.set_key(key_down)
            
            # Calculate duration of PREVIOUS state (TCI protocol semantics)
            if transmission_start is None:
                transmission_start = now
                previous_duration_ms = 0
            else:
                previous_duration_ms = int((now - last_event_time) * 1000)
            
            # Cap duration to prevent overflow
            previous_duration_ms = min(previous_duration_ms, 65535)
            
            # Update state
            last_event_time = now
            self.key_down = key_down
            
            # Send keying event with PREVIOUS state's duration
            if self.on_key_event:
                await self.on_key_event(key_down, previous_duration_ms)
            
            self.logger.debug(
                f"Iambic: {'DN' if key_down else 'UP'}, "
                f"element={int(duration_ms)}ms, prev={previous_duration_ms}ms"
            )
        
        # Pre-arm TX callback - called once before first element to enable TX
        async def pre_arm_tx():
            """Setup TX before first keying element (allows TX settling time)"""
            if self.on_tx_start:
                await self.on_tx_start()
        
        # Main iambic loop - uses blocking sleep for precise timing
        while self.running:
            try:
                # Let iambic keyer generate elements (blocking)
                is_active = await self.iambic_keyer.update(paddle_reader, send_element, pre_arm_tx)
                
                if not is_active:
                    # Keyer went idle, reset timing for next transmission
                    transmission_start = None
                    # Small sleep when idle to avoid spinning
                    await asyncio.sleep(0.001)
                    
            except OSError as e:
                # Device disconnected
                self.logger.warning(f"USB paddle disconnected: {e}")
                self.running = False
                if on_disconnect:
                    await on_disconnect()
                break
            except Exception as e:
                self.logger.error(f"Error in iambic keyer loop: {e}")
                await asyncio.sleep(0.1)
    
    def set_sidetone(self, sidetone):
        """Set sidetone generator for immediate audio feedback during iambic keying"""
        self._sidetone = sidetone
    
    def stop(self):
        """Stop polling loop"""
        self.running = False
    
    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.stop()
        self.disconnect()


class IambicKeyer:
    """
    Iambic keyer logic (Mode A and Mode B)
    
    NOTE: Use this only if TCI server doesn't handle iambic keying.
    Test with straight key mode first.
    """
    
    # State constants
    IDLE = 0
    DIT = 1
    DAH = 2
    
    def __init__(self, wpm: int = 20, mode: str = 'B'):
        """
        Initialize iambic keyer
        
        Args:
            wpm: Speed in words per minute
            mode: 'A' or 'B'
        """
        self.mode = mode
        self.set_speed(wpm)
        
        # State
        self.state = self.IDLE
        self.dit_memory = False
        self.dah_memory = False
    
    def set_speed(self, wpm: int):
        """Set keyer speed in WPM"""
        self.wpm = wpm
        self.dit_duration = 1200 / wpm  # milliseconds
        self.dah_duration = self.dit_duration * 3
        self.element_space = self.dit_duration
    
    async def update(self, paddle_reader, send_element_callback, pre_arm_callback=None):
        """
        Main keyer update - generates iambic elements
        
        Args:
            paddle_reader: function() -> (dit: bool, dah: bool) - SYNC function
            send_element_callback: async function(key_down: bool, duration_ms: float)
            pre_arm_callback: async function() - called ONCE before first element (for TX pre-arm)
        
        Returns:
            bool - True if keyer active, False if idle
        """
        # Read current paddle states
        dit_paddle, dah_paddle = paddle_reader()
        
        # State: IDLE
        if self.state == self.IDLE:
            if dit_paddle:
                self.dit_memory = False
                self.dah_memory = False
                self.state = self.DIT
                
                # Pre-arm TX before first element (allows settling time)
                if pre_arm_callback:
                    await pre_arm_callback()
                
                # Send dit
                await send_element_callback(True, self.dit_duration)
                time.sleep(self.dit_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                time.sleep(self.element_space / 1000.0)
                
                # Check for opposite paddle (Mode B memory)
                dit_paddle, dah_paddle = paddle_reader()
                if self.mode == 'B' and dah_paddle:
                    self.dah_memory = True
                    
            elif dah_paddle:
                self.dit_memory = False
                self.dah_memory = False
                self.state = self.DAH
                
                # Pre-arm TX before first element (allows settling time)
                if pre_arm_callback:
                    await pre_arm_callback()
                
                # Send dah
                await send_element_callback(True, self.dah_duration)
                time.sleep(self.dah_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                time.sleep(self.element_space / 1000.0)
                
                # Check for opposite paddle (Mode B memory)
                dit_paddle, dah_paddle = paddle_reader()
                if self.mode == 'B' and dit_paddle:
                    self.dit_memory = True
            else:
                return False  # Still idle
        
        # State: DIT - just sent a dit
        elif self.state == self.DIT:
            # Sample paddles during element space
            dit_paddle, dah_paddle = paddle_reader()
            if dit_paddle:
                self.dit_memory = True
            if dah_paddle:
                self.dah_memory = True
            
            # Decide next element - DAH has priority (opposite paddle)
            if self.dah_memory:
                self.dah_memory = False
                self.state = self.DAH
                
                await send_element_callback(True, self.dah_duration)
                time.sleep(self.dah_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                time.sleep(self.element_space / 1000.0)
                
                # Mode B: Check for opposite paddle during element
                dit_paddle, dah_paddle = paddle_reader()
                if self.mode == 'B' and dit_paddle:
                    self.dit_memory = True
                    
            elif self.dit_memory:
                self.dit_memory = False
                self.state = self.DIT
                
                await send_element_callback(True, self.dit_duration)
                time.sleep(self.dit_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                time.sleep(self.element_space / 1000.0)
                
                # Mode B: Check for opposite paddle during element
                dit_paddle, dah_paddle = paddle_reader()
                if self.mode == 'B' and dah_paddle:
                    self.dah_memory = True
            else:
                # No memory, return to idle
                self.state = self.IDLE
                return False
        
        # State: DAH - just sent a dah
        elif self.state == self.DAH:
            # Sample paddles during element space
            dit_paddle, dah_paddle = paddle_reader()
            if dit_paddle:
                self.dit_memory = True
            if dah_paddle:
                self.dah_memory = True
            
            # Decide next element - DIT has priority (opposite paddle)
            if self.dit_memory:
                self.dit_memory = False
                self.state = self.DIT
                
                await send_element_callback(True, self.dit_duration)
                time.sleep(self.dit_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                time.sleep(self.element_space / 1000.0)
                
                # Mode B: Check for opposite paddle during element
                dit_paddle, dah_paddle = paddle_reader()
                if self.mode == 'B' and dah_paddle:
                    self.dah_memory = True
                    
            elif self.dah_memory:
                self.dah_memory = False
                self.state = self.DAH
                
                await send_element_callback(True, self.dah_duration)
                time.sleep(self.dah_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                time.sleep(self.element_space / 1000.0)
                
                # Mode B: Check for opposite paddle during element
                dit_paddle, dah_paddle = paddle_reader()
                if self.mode == 'B' and dit_paddle:
                    self.dit_memory = True
            else:
                # No memory, return to idle
                self.state = self.IDLE
                return False
        
        return True
