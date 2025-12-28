#!/usr/bin/env python3
"""
TCI Protocol Client

WebSocket client for TCI (Transceiver Command Interface) protocol.
Handles connection, command sending, and response parsing.
"""

import asyncio
import websockets
import logging
from typing import Optional, Callable


class TCIClient:
    """Async WebSocket client for TCI protocol"""
    
    def __init__(self, host: str, port: int, trx_number: int = 0):
        """
        Initialize TCI client
        
        Args:
            host: TCI server hostname/IP
            port: TCI server port
            trx_number: Transceiver number (default 0)
        """
        self.host = host
        self.port = port
        self.trx_number = trx_number
        self.uri = f"ws://{host}:{port}"
        
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.ready = False
        self.running = False
        
        # State tracking (read from TCI server on connect)
        self.drive_level = 0  # Drive level (%), set from server on connect
        self.current_mode = None  # Current modulation mode (CW, USB, LSB, etc.)
        
        # Callbacks for events
        self.on_ready: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None
        self.on_message: Optional[Callable[[str], None]] = None
        
        self.logger = logging.getLogger("TCIClient")
    
    async def connect(self, timeout: float = 5.0) -> bool:
        """
        Connect to TCI server and wait for READY
        
        Args:
            timeout: Connection timeout in seconds
            
        Returns:
            True if connected and ready, False otherwise
        """
        try:
            self.logger.info(f"Connecting to TCI server at {self.uri}")
            self.websocket = await asyncio.wait_for(
                websockets.connect(self.uri),
                timeout=timeout
            )
            self.logger.info("Connected to TCI server")
            
            # Wait for READY; message
            ready_timeout = 10.0
            start_time = asyncio.get_event_loop().time()
            
            while (asyncio.get_event_loop().time() - start_time) < ready_timeout:
                try:
                    message = await asyncio.wait_for(self.websocket.recv(), timeout=1.0)
                    self.logger.debug(f"RX: {message.strip()}")
                    
                    if message.strip().upper() == "READY;":
                        self.ready = True
                        self.logger.info("TCI server is READY")
                        if self.on_ready:
                            self.on_ready()
                        return True
                    
                    # Process other initialization messages
                    if self.on_message:
                        self.on_message(message.strip())
                        
                except asyncio.TimeoutError:
                    continue
            
            self.logger.error("Timeout waiting for READY; message")
            await self.disconnect()
            return False
            
        except asyncio.TimeoutError:
            self.logger.error(f"Connection timeout after {timeout}s")
            return False
        except Exception as e:
            self.logger.error(f"Connection error: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from TCI server"""
        self.running = False
        self.ready = False
        
        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("Disconnected from TCI server")
            except Exception as e:
                self.logger.debug(f"Error during disconnect: {e}")
            finally:
                self.websocket = None
        
        if self.on_disconnect:
            self.on_disconnect()
    
    async def send_command(self, command: str):
        """
        Send TCI command
        
        Args:
            command: TCI command string (without trailing semicolon)
        """
        if not self.websocket or not self.ready:
            self.logger.warning(f"Cannot send command (not ready): {command}")
            return
        
        try:
            # Ensure command ends with semicolon
            if not command.endswith(';'):
                command += ';'
            
            self.logger.debug(f"TX: {command}")
            await self.websocket.send(command)
            
        except Exception as e:
            self.logger.error(f"Error sending command: {e}")
            await self.disconnect()
    
    async def receive_loop(self):
        """Background task to receive and process TCI messages"""
        self.running = True
        
        while self.running and self.websocket:
            try:
                message = await asyncio.wait_for(self.websocket.recv(), timeout=1.0)
                self.logger.debug(f"RX: {message.strip()}")
                
                if self.on_message:
                    self.on_message(message.strip())
                    
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                self.logger.warning("Connection closed by server")
                await self.disconnect()
                break
            except Exception as e:
                self.logger.error(f"Error in receive loop: {e}")
                await self.disconnect()
                break
    
    # =========================================================================
    # TCI Command Methods
    # =========================================================================
    
    async def set_mode_cw(self, mode: str = "CW"):
        """
        Set radio to CW mode
        
        Args:
            mode: CW mode (CW, CWL, or CWU)
        """
        await self.send_command(f"MODULATION:{self.trx_number},{mode}")
        self.current_mode = mode.upper()
    
    def parse_modulation_from_message(self, message: str):
        """
        Parse MODULATION message and update current mode
        
        Args:
            message: TCI message like 'MODULATION:0,CW;'
        """
        try:
            # Format: MODULATION:trx,mode;
            parts = message.rstrip(';').split(':')
            if len(parts) >= 2:
                args = parts[1].split(',')
                if len(args) >= 2:
                    trx = int(args[0])
                    if trx == self.trx_number:
                        self.current_mode = args[1].upper()
                        self.logger.debug(f"Mode updated: {self.current_mode}")
        except Exception as e:
            self.logger.debug(f"Error parsing MODULATION: {e}")
    
    async def set_cw_speed(self, wpm: int):
        """
        Set CW speed in WPM
        
        Args:
            wpm: Speed in words per minute
        """
        await self.send_command(f"CW_MACROS_SPEED:{wpm}")
    
    async def send_cw_macros(self, text: str, force_ptt: bool = False):
        """
        Send CW text macro (text-to-morse conversion)
        
        Args:
            text: Text to send as CW
                  Special: > = faster, < = slower, |SK| = prosign
            force_ptt: If True, explicitly enable PTT before sending (workaround for focus issues)
        """
        # Escape reserved characters in TCI protocol
        text = text.replace(':', '^')  # : → ^
        text = text.replace(',', '~')  # , → ~
        text = text.replace(';', '*')  # ; → *
        
        # Workaround for ExpertSDR3 event loop bug:
        # The VFO query doesn't help - ExpertSDR3 doesn't process ANY websocket 
        # messages until a Windows UI event occurs. This is a bug in ExpertSDR3.
        
        # Workaround: Force PTT on if ExpertSDR3 requires focus
        if force_ptt:
            await self.send_command(f"TRX:{self.trx_number},true")
            await asyncio.sleep(0.05)  # Small delay for PTT to engage
        
        await self.send_command(f"cw_macros:{self.trx_number},{text}")
    
    async def stop_cw_macros(self):
        """Stop CW macro transmission immediately"""
        await self.send_command("cw_macros_stop")
    
    async def send_keyer(self, key_down: bool, previous_duration_ms: int):
        """
        Send manual keyer command (for physical paddle/key)
        
        Args:
            key_down: Current key state (True=down, False=up)
            previous_duration_ms: Duration of PREVIOUS state in milliseconds
        """
        state = "true" if key_down else "false"
        await self.send_command(f"KEYER:{self.trx_number},{state},{previous_duration_ms}")
    
    async def set_trx(self, transmit: bool):
        """
        Set transmit/receive state
        
        Args:
            transmit: True for TX, False for RX
        """
        state = "true" if transmit else "false"
        await self.send_command(f"TRX:{self.trx_number},{state}")
    
    async def set_ptt(self, transmit: bool):
        """
        Set PTT (Push-To-Talk) state - alias for set_trx()
        
        Args:
            transmit: True for TX, False for RX
        """
        await self.set_trx(transmit)
    
    async def set_drive(self, level: int):
        """
        Set drive (power) level
        
        Args:
            level: Drive level 0-100 (%)
        """
        level = max(0, min(100, level))
        await self.send_command(f"DRIVE:{self.trx_number},{level}")
    
    def parse_drive_from_message(self, message: str):
        """
        Parse drive level from TCI message and update internal state
        
        Args:
            message: TCI message string (e.g., "drive:0,50;")
        """
        if message.lower().startswith(f"drive:{self.trx_number},"):
            try:
                # Parse "drive:0,50;" -> 50
                parts = message.rstrip(';').split(',')
                if len(parts) >= 2:
                    self.drive_level = int(parts[1])
                    self.logger.debug(f"Drive level updated: {self.drive_level}%")
            except (ValueError, IndexError):
                pass
