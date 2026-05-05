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
        self.receive_only = False  # Set by server; blocks TX commands when True
        
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
                    # Handle both bytes and string from websocket
                    if isinstance(message, bytes):
                        message = message.decode('utf-8')
                    self.logger.debug(f"RX: {message.strip()}")
                    
                    # A frame may pack multiple commands: "VFO:...;TRX:...;READY;"
                    cmds = [c.strip() for c in message.split(';') if c.strip()]
                    if any(c.upper() == 'READY' for c in cmds):
                        self.ready = True
                        self.logger.info("TCI server is READY")
                        # Dispatch any other commands packed in the same frame
                        for cmd in cmds:
                            if cmd.upper() != 'READY':
                                self._dispatch_commands(cmd)
                        if self.on_ready:
                            self.on_ready()
                        return True
                    
                    # Not READY yet — dispatch all commands in this frame
                    self._dispatch_commands(message)
                        
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
                
                # Check if this is a binary frame (IQ/audio stream)
                if isinstance(message, bytes):
                    # Binary frames are typically 64+ bytes (StreamHeader + data)
                    # Text commands are usually < 100 bytes and ASCII
                    if len(message) >= 64:
                        # This is a binary stream packet (IQ_STREAM, RX_AUDIO_STREAM, etc.)
                        # Skip it - we don't need audio/IQ data for CW control
                        self.logger.debug(f"Skipping binary stream packet ({len(message)} bytes)")
                        continue
                    else:
                        # Short binary message - try to decode as text
                        try:
                            message = message.decode('utf-8')
                        except UnicodeDecodeError:
                            self.logger.warning(f"Received non-UTF8 binary data ({len(message)} bytes), skipping")
                            continue
                
                # Process text command
                if not message:
                    continue
                    
                self.logger.debug(f"RX: {message.strip()}")
                self._dispatch_commands(message)
                    
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
    
    def _dispatch_commands(self, raw_frame: str):
        """Split a raw TCI frame by ';' and dispatch each command individually.
        
        Handles lifecycle messages (stop/start) and receive_only state internally.
        Forwards all other commands to the on_message callback.
        """
        for cmd in raw_frame.split(';'):
            cmd = cmd.strip()
            if not cmd:
                continue

            cmd_lower = cmd.lower()

            # Lifecycle: server stopping
            if cmd_lower == 'stop':
                self.logger.warning("TCI server sent 'stop' — marking not ready")
                self.ready = False
                if self.on_disconnect:
                    self.on_disconnect()
                continue

            # Lifecycle: server (re)started
            if cmd_lower == 'start':
                self.logger.info("TCI server sent 'start'")
                self.ready = True
                continue

            # Track receive_only state
            if cmd_lower.startswith('receive_only:'):
                args = cmd[len('receive_only:'):].split(',')
                if len(args) >= 2:
                    try:
                        if int(args[0]) == self.trx_number:
                            self.receive_only = (args[1].lower() == 'true')
                            self.logger.info(f"Receive-only mode: {self.receive_only}")
                    except (ValueError, IndexError):
                        pass
                # Fall through so on_message still sees it

            # Track current modulation mode (needed for mode guard in send_cw_macros)
            if cmd_lower.startswith('modulation:'):
                self.parse_modulation_from_message(cmd)

            if self.on_message:
                self.on_message(cmd)

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
        Parse a single MODULATION command and update current mode.
        Expected format: "MODULATION:trx,mode" (semicolon already stripped by _dispatch_commands)
        """
        if isinstance(message, bytes):
            message = message.decode('utf-8')
        try:
            cmd = message.rstrip(';')
            if ':' not in cmd:
                return
            args = cmd.split(':', 1)[1].split(',')
            if len(args) >= 2 and int(args[0]) == self.trx_number:
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
        await self.send_command(f"CW_KEYER_SPEED:{wpm}")
        await self.send_command(f"CW_MACROS_SPEED:{wpm}")
    
    CW_MODES = {'CW', 'CWL', 'CWU'}

    async def send_cw_macros(self, text: str, force_ptt: bool = False) -> bool:
        """
        Send CW text macro (text-to-morse conversion).

        Returns:
            True if the command was dispatched, False if blocked.
        """
        if self.receive_only:
            self.logger.warning("Cannot send CW macro: receive-only mode active")
            return False

        # Block macro if radio is NOT in a CW mode — do NOT auto-switch.
        # Auto-switching to CW would abort any active DIGU/digital session (e.g.
        # MSHV) running on the same TCI server. The user must switch to CW first.
        if self.current_mode is not None and self.current_mode not in self.CW_MODES:
            self.logger.warning(
                f"CW macro blocked: radio is in {self.current_mode} mode. "
                f"Switch to CW/CWL/CWU first."
            )
            return False

        # Escape reserved TCI characters in the payload
        text = text.replace(':', '^')  # : → ^
        text = text.replace(',', '~')  # , → ~
        text = text.replace(';', '*')  # ; → *

        # Workaround: send TRX:true first to wake ExpertSDR3's event loop.
        # Mode switch above ensures the radio is in CW before TX is armed.
        if force_ptt:
            await self.send_command(f"TRX:{self.trx_number},true")
            await asyncio.sleep(0.05)  # Small delay for PTT to settle

        await self.send_command(f"cw_macros:{self.trx_number},{text}")
        return True
    
    async def stop_cw_macros(self):
        """Stop CW macro transmission immediately"""
        await self.send_command("cw_macros_stop")
    
    async def send_keyer(self, key_down: bool, previous_duration_ms: int, force_ptt: bool = False, settle_time_ms: int = 0):
        """
        Send manual keyer command (for physical paddle/key)
        
        Args:
            key_down: Current key state (True=down, False=up)
            previous_duration_ms: Duration of PREVIOUS state in milliseconds
            force_ptt: If True, send TRX command before KEYER (for Break-in workaround)
            settle_time_ms: Delay in ms after TRX command (only used if force_ptt=True and key_down=True)
        """
        if self.receive_only:
            self.logger.warning("Cannot send keyer: receive-only mode active")
            return

        # Force PTT on before KEYER command if requested
        if force_ptt and key_down:
            await self.send_command(f"TRX:{self.trx_number},true")
            # Wait for TX to settle before sending KEYER
            if settle_time_ms > 0:
                await asyncio.sleep(settle_time_ms / 1000.0)
        
        state = "true" if key_down else "false"
        await self.send_command(f"KEYER:{self.trx_number},{state},{previous_duration_ms}")
    
    async def set_trx(self, transmit: bool):
        """
        Set transmit/receive state
        
        Args:
            transmit: True for TX, False for RX
        """
        if self.receive_only and transmit:
            self.logger.warning("Cannot set TX: receive-only mode active")
            return
        state = "true" if transmit else "false"
        self.logger.debug(f"Sending TRX command: TRX:{self.trx_number},{state}")
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
        Parse drive level from a single TCI command and update internal state.
        Expected format: "drive:trx,level" (semicolon already stripped by _dispatch_commands)
        """
        if isinstance(message, bytes):
            message = message.decode('utf-8')
        try:
            cmd = message.rstrip(';')
            if ':' not in cmd:
                return
            args = cmd.split(':', 1)[1].split(',')
            if len(args) >= 2 and int(args[0]) == self.trx_number:
                self.drive_level = int(args[1])
                self.logger.debug(f"Drive level updated: {self.drive_level}%")
        except (ValueError, IndexError):
            pass
