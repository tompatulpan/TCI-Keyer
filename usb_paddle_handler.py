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
                 debug: bool = False):
        """
        Initialize USB paddle handler
        
        Args:
            device_path: Explicit device path or None for auto-detect
            vendor_id: USB Vendor ID
            product_id: USB Product ID
            debug: Enable debug output
        """
        self.hid_reader = XiaoHIDReader(
            device_path=device_path,
            vid=vendor_id,
            pid=product_id,
            debug=debug
        )
        
        self.running = False
        self.poll_interval = 0.001  # 1ms polling
        
        # Timing state
        self.key_down = False
        self.last_event_time: Optional[float] = None
        self.transmission_start: Optional[float] = None
        
        # Callbacks
        self.on_key_event: Optional[Callable[[bool, int], None]] = None  # (key_down, prev_duration_ms)
        
        self.logger = logging.getLogger("USBPaddleHandler")
    
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
    
    def disconnect(self):
        """Disconnect from USB HID device"""
        self.hid_reader.close()
        self.logger.info("USB paddle disconnected")
    
    async def poll_loop(self):
        """
        Main polling loop - reads paddle state and measures timing
        
        This implements the TCI KEYER timing semantics:
        - Previous duration is sent with each state change
        - First event always has 0ms previous duration
        """
        self.running = True
        self.logger.info("USB paddle polling started")
        
        while self.running:
            try:
                # Read paddle states (dit, dah)
                dit, dah = self.hid_reader.read_paddles()
                
                # Combine for straight key mode (either paddle = key down)
                new_key_down = dit or dah
                
                # Detect state change
                if new_key_down != self.key_down:
                    now = time.perf_counter()
                    
                    # Calculate PREVIOUS state duration (TCI semantics)
                    if self.transmission_start is None:
                        # First event - no previous duration
                        self.transmission_start = now
                        self.last_event_time = now
                        previous_duration_ms = 0
                    else:
                        # Calculate duration since last event
                        duration_sec = now - self.last_event_time
                        previous_duration_ms = int(duration_sec * 1000)
                        
                        # Cap at 16-bit max (65535ms = 65.5 seconds)
                        previous_duration_ms = min(previous_duration_ms, 65535)
                        
                        self.last_event_time = now
                    
                    # Update state
                    self.key_down = new_key_down
                    
                    # Call callback with key state and previous duration
                    if self.on_key_event:
                        await self.on_key_event(new_key_down, previous_duration_ms)
                    
                    self.logger.debug(
                        f"Key {'DOWN' if new_key_down else 'UP'}, "
                        f"prev_duration={previous_duration_ms}ms"
                    )
                    
                    # Reset transmission tracking when returning to idle
                    if not new_key_down:
                        # Check if we've been idle for a while (end of transmission)
                        # This helps reset timing for next transmission
                        pass  # Keep transmission_start for now
                
                # Sleep to achieve polling rate
                await asyncio.sleep(self.poll_interval)
                
            except Exception as e:
                self.logger.error(f"Error in USB paddle poll loop: {e}")
                await asyncio.sleep(0.1)
        
        self.logger.info("USB paddle polling stopped")
    
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
    
    async def update(self, paddle_reader, send_element_callback):
        """
        Main keyer update - generates iambic elements
        
        Args:
            paddle_reader: async function() -> (dit: bool, dah: bool)
            send_element_callback: async function(key_down: bool, duration_ms: float)
        
        Returns:
            bool - True if keyer active, False if idle
        """
        # Read current paddle states
        dit_paddle, dah_paddle = await paddle_reader()
        
        # State: IDLE
        if self.state == self.IDLE:
            if dit_paddle:
                self.dit_memory = False
                self.dah_memory = False
                self.state = self.DIT
                
                # Send dit
                await send_element_callback(True, self.dit_duration)
                await asyncio.sleep(self.dit_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                await asyncio.sleep(self.element_space / 1000.0)
                
                # Check for opposite paddle (Mode B memory)
                dit_paddle, dah_paddle = await paddle_reader()
                if self.mode == 'B' and dah_paddle:
                    self.dah_memory = True
                    
            elif dah_paddle:
                self.dit_memory = False
                self.dah_memory = False
                self.state = self.DAH
                
                # Send dah
                await send_element_callback(True, self.dah_duration)
                await asyncio.sleep(self.dah_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                await asyncio.sleep(self.element_space / 1000.0)
                
                # Check for opposite paddle (Mode B memory)
                dit_paddle, dah_paddle = await paddle_reader()
                if self.mode == 'B' and dit_paddle:
                    self.dit_memory = True
            else:
                return False  # Still idle
        
        # State: DIT - just sent a dit
        elif self.state == self.DIT:
            # Sample paddles
            dit_paddle, dah_paddle = await paddle_reader()
            if dit_paddle:
                self.dit_memory = True
            if dah_paddle:
                self.dah_memory = True
            
            # Decide next element
            if self.dah_memory:
                self.dah_memory = False
                self.state = self.DAH
                
                await send_element_callback(True, self.dah_duration)
                await asyncio.sleep(self.dah_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                await asyncio.sleep(self.element_space / 1000.0)
                
                dit_paddle, dah_paddle = await paddle_reader()
                if self.mode == 'B' and dit_paddle:
                    self.dit_memory = True
                    
            elif self.dit_memory:
                self.dit_memory = False
                self.state = self.DIT
                
                await send_element_callback(True, self.dit_duration)
                await asyncio.sleep(self.dit_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                await asyncio.sleep(self.element_space / 1000.0)
                
                dit_paddle, dah_paddle = await paddle_reader()
                if self.mode == 'B' and dah_paddle:
                    self.dah_memory = True
            else:
                self.state = self.IDLE
                return False
        
        # State: DAH - just sent a dah
        elif self.state == self.DAH:
            # Sample paddles
            dit_paddle, dah_paddle = await paddle_reader()
            if dit_paddle:
                self.dit_memory = True
            if dah_paddle:
                self.dah_memory = True
            
            # Decide next element
            if self.dit_memory:
                self.dit_memory = False
                self.state = self.DIT
                
                await send_element_callback(True, self.dit_duration)
                await asyncio.sleep(self.dit_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                await asyncio.sleep(self.element_space / 1000.0)
                
                dit_paddle, dah_paddle = await paddle_reader()
                if self.mode == 'B' and dah_paddle:
                    self.dah_memory = True
                    
            elif self.dah_memory:
                self.dah_memory = False
                self.state = self.DAH
                
                await send_element_callback(True, self.dah_duration)
                await asyncio.sleep(self.dah_duration / 1000.0)
                await send_element_callback(False, self.element_space)
                await asyncio.sleep(self.element_space / 1000.0)
                
                dit_paddle, dah_paddle = await paddle_reader()
                if self.mode == 'B' and dit_paddle:
                    self.dit_memory = True
            else:
                self.state = self.IDLE
                return False
        
        return True
